from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ..models import PublishJob
from .config import load_marketplace_ui_config
from .content_validation import ContentValidationResult, validate_listing_content
from .detector import detect_marketplace_state
from .diagnostics import DiagnosticBundleWriter, build_context
from .fingerprints import detect_ui_variant
from .guard import (
    ActionGuardDecision,
    ActionPlan,
    GuardResult,
    build_fill_plan,
    evaluate_action_plan,
)
from .popups import detect_known_popup, handle_known_popup
from .retry import retry_find
from .selectors import SelectorMatch, click_target_for_node, find_element
from .snapshot import UISnapshot, bounds_center, snapshot_from_xml
from .states import MarketplaceState
from .validator import FieldValidation, validate_field_for_target
from .verifier import verify_field_value, verify_publish_pressed, verify_transition
from .vision import (
    DisabledVisionProvider,
    ScreenVisionProvider,
    VisionAuditLog,
    VisionRequest,
    load_vision_provider,
)


class MarketplaceSafeStop(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        state: MarketplaceState,
        publication_status: str = "needs_review",
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.state = state
        self.publication_status = publication_status


@dataclass
class MarketplaceSession:
    device: Any
    job: PublishJob
    ui_cfg: dict[str, Any]
    publication_job_id: str = ""
    last_successful_state: MarketplaceState | None = None
    current_state: MarketplaceState = MarketplaceState.UNKNOWN_SCREEN
    actions_log: list[dict[str, Any]] | None = None
    diagnostics: DiagnosticBundleWriter | None = None
    vision: ScreenVisionProvider | None = None
    vision_audit: VisionAuditLog | None = None
    _snapshot: UISnapshot | None = None

    @classmethod
    def create(
        cls,
        device,
        job: PublishJob,
        publisher_cfg: dict[str, Any],
        *,
        publication_job_id: str = "",
    ) -> MarketplaceSession:
        ui_cfg = load_marketplace_ui_config(publisher_cfg)
        session = cls(
            device=device,
            job=job,
            ui_cfg=ui_cfg,
            publication_job_id=publication_job_id or job.page_id,
            actions_log=[],
            diagnostics=DiagnosticBundleWriter(ui_cfg),
            vision=load_vision_provider(ui_cfg),
        )
        session.refresh_snapshot()
        session.current_state = detect_marketplace_state(session.snapshot)
        return session

    @property
    def snapshot(self) -> UISnapshot:
        if self._snapshot is None:
            self.refresh_snapshot()
        assert self._snapshot is not None
        return self._snapshot

    def refresh_snapshot(self) -> UISnapshot:
        if self._snapshot is None:
            self._snapshot = UISnapshot(xml="", nodes=[])
        previous = self.current_state
        self._snapshot.refresh_from_device(self.device)
        self.current_state = detect_marketplace_state(self._snapshot)
        variant = detect_ui_variant(previous, self._snapshot)
        if variant:
            self._log_action(
                {
                    "type": "ui_variant",
                    "variant": variant,
                    "state": self.current_state.value,
                }
            )
        popup = detect_known_popup(self._snapshot)
        if popup:
            handled = handle_known_popup(self.device, popup)
            self._log_action(
                {
                    "type": "known_popup",
                    "popup_id": popup.popup_id,
                    "handled": handled,
                }
            )
            if handled:
                self._snapshot.refresh_from_device(self.device)
                self.current_state = detect_marketplace_state(self._snapshot)
        return self._snapshot

    def _log_action(self, entry: dict[str, Any]) -> None:
        if self.actions_log is not None:
            self.actions_log.append(entry)

    def require_state(self, *allowed: MarketplaceState) -> None:
        if self.current_state not in allowed:
            self.safe_stop(
                f"unexpected_state:{self.current_state.value}",
                publication_status="needs_review",
            )

    def safe_stop(
        self,
        reason: str,
        *,
        publication_status: str = "needs_review",
        plan: ActionPlan | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self.save_diagnostics(reason=reason, plan=plan, exc=exc)
        raise MarketplaceSafeStop(
            reason,
            state=self.current_state,
            publication_status=publication_status,
        )

    def save_diagnostics(
        self,
        *,
        reason: str,
        plan: ActionPlan | None = None,
        exc: BaseException | None = None,
    ) -> None:
        if self.diagnostics is None:
            return
        resolution = (self.snapshot.width, self.snapshot.height)
        context = build_context(
            listing_id=self.job.object_id,
            publication_job_id=self.publication_job_id,
            current_state=self.current_state,
            last_successful_state=self.last_successful_state,
            plan=plan,
            reason=reason,
            activity=self.snapshot.activity,
            resolution=resolution if resolution[0] else None,
            exc=exc,
        )
        screenshot_bytes = None
        if self.ui_cfg.get("diagnostics", {}).get("save_screenshot", True):
            try:
                screenshot_bytes = self.device.screenshot(format="raw")
            except Exception:
                screenshot_bytes = None
        self.diagnostics.save(
            context=context,
            xml=self.snapshot.xml,
            screenshot_bytes=screenshot_bytes,
            actions=self.actions_log,
        )

    def evaluate_guard(self, plan: ActionPlan) -> GuardResult:
        return evaluate_action_plan(plan, self.ui_cfg)

    def guarded_fill_field(
        self,
        target: str,
        value: str,
        *,
        value_type: str = "text",
        set_text: Callable[[FieldValidation, str], None],
        read_back: Callable[[], str] | None = None,
    ) -> None:
        retries = int(self.ui_cfg.get("retries", {}).get("input_verification", 2))
        for attempt in range(retries):
            self.refresh_snapshot()
            validation = validate_field_for_target(
                self.snapshot,
                target,
                ui_cfg=self.ui_cfg,
                state=self.current_state,
            )
            if not validation.ok or validation.match is None:
                if self._try_vision_select(target):
                    validation = validate_field_for_target(
                        self.snapshot,
                        target,
                        ui_cfg=self.ui_cfg,
                        state=self.current_state,
                    )
                if not validation.ok or validation.match is None:
                    self.safe_stop("semantic_validation_failed", plan=None)

            plan = build_fill_plan(
                state=self.current_state,
                target=target,
                value_type=value_type,
                confidence=validation.confidence,
                evidence=list(validation.evidence),
            )
            guard = self.evaluate_guard(plan)
            if guard.decision == ActionGuardDecision.SAFE_STOP:
                self.safe_stop(guard.reason or "guard_safe_stop", plan=plan)
            if guard.decision == ActionGuardDecision.RECHECK:
                self.refresh_snapshot()
                validation = validate_field_for_target(
                    self.snapshot,
                    target,
                    ui_cfg=self.ui_cfg,
                    state=self.current_state,
                )
                plan = build_fill_plan(
                    state=self.current_state,
                    target=target,
                    value_type=value_type,
                    confidence=validation.confidence,
                    evidence=list(validation.evidence),
                )
                guard = self.evaluate_guard(plan)
                if guard.decision != ActionGuardDecision.EXECUTE:
                    self.safe_stop("recheck_failed", plan=plan)

            self._click_node(validation.match, plan=plan)
            set_text(validation, value)
            self.refresh_snapshot()
            ok, reason = verify_field_value(
                self.device,
                self.snapshot,
                target=target,
                expected=value,
                ui_cfg=self.ui_cfg,
                state=self.current_state,
            )
            if ok:
                self.last_successful_state = self.current_state
                self._log_action(
                    {
                        "type": "fill_field",
                        "target": target,
                        "attempt": attempt + 1,
                        "confidence": plan.confidence,
                    }
                )
                return
            if read_back is not None:
                actual = read_back()
                if actual.strip() == value.strip():
                    self.last_successful_state = self.current_state
                    return
            if attempt + 1 >= retries:
                self.safe_stop(f"input_verification_failed:{reason}", plan=plan)
            time.sleep(0.3)

    def _click_node(self, match: SelectorMatch | None, *, plan: ActionPlan) -> None:
        if match is None:
            self.safe_stop("missing_click_target", plan=plan)
        method, point = click_target_for_node(match.node)
        if method == "bounds" and point is not None:
            if self._click_via_device(match, point, plan=plan):
                return
        self.safe_stop("click_target_unavailable", plan=plan)

    def _selector_exists(self, selector) -> bool:
        try:
            return selector.exists(timeout=0.3) is True
        except Exception:
            return False

    def _click_via_device(self, match: SelectorMatch, point: tuple[int, int], *, plan: ActionPlan) -> bool:
        node = match.node
        if node.resource_id:
            selector = self.device(resourceId=node.resource_id)
            if self._selector_exists(selector):
                selector.click()
                self._log_action({"type": "click", "method": "resource-id", "target": match.element_key})
                return True
        if node.content_desc:
            selector = self.device(description=node.content_desc)
            if self._selector_exists(selector):
                selector.click()
                self._log_action({"type": "click", "method": "content-desc", "target": match.element_key})
                return True
        if node.text:
            selector = self.device(text=node.text)
            if self._selector_exists(selector):
                selector.click()
                self._log_action({"type": "click", "method": "text", "target": match.element_key})
                return True

        if node.bounds and point is not None:
            self.device.click(point[0], point[1])
            self._log_action(
                {
                    "type": "click",
                    "method": "node_bounds",
                    "target": match.element_key,
                    "point": point,
                }
            )
            return True

        coordinate_enabled = bool(
            self.ui_cfg.get("vision", {}).get("coordinate_fallback_enabled", False)
        )
        if coordinate_enabled and point is not None:
            threshold = float(
                self.ui_cfg.get("confidence", {}).get("coordinate_execute", 0.97)
            )
            if plan.confidence >= threshold:
                self.device.click(point[0], point[1])
                self._log_action(
                    {
                        "type": "click",
                        "method": "coordinate_fallback",
                        "target": match.element_key,
                        "point": point,
                    }
                )
                return True
        return False

    def click_profile_element(self, element_key: str) -> None:
        retries = int(self.ui_cfg.get("retries", {}).get("find_element", 3))

        def _find() -> SelectorMatch | None:
            self.refresh_snapshot()
            return find_element(self.snapshot, element_key)

        match = retry_find(_find, attempts=retries)
        if match is None and self._try_vision_select(element_key):
            match = find_element(self.snapshot, element_key)
        if match is None:
            self.safe_stop(f"element_not_found:{element_key}")
        plan = ActionPlan(
            state=self.current_state,
            action="detect_only",
            target=element_key,
            value_type="none",
            confidence=match.confidence,
            evidence=list(match.evidence),
        )
        guard = self.evaluate_guard(plan)
        if guard.decision == ActionGuardDecision.SAFE_STOP:
            self.safe_stop(guard.reason or "guard_safe_stop", plan=plan)
        self._click_node(match, plan=plan)
        self.refresh_snapshot()

    def verify_next_transition(self, previous_state: MarketplaceState) -> None:
        ok, new_state, reason = verify_transition(
            self.refresh_snapshot,
            previous_state=previous_state,
            ui_cfg=self.ui_cfg,
        )
        if not ok:
            self.safe_stop(f"transition_failed:{reason}")
        self.current_state = new_state

    def final_content_validation(
        self,
        *,
        title_value: str,
        price_value: str,
        description_value: str,
        media_count: int,
        expected_media_count: int,
        location_value: str = "",
    ) -> ContentValidationResult:
        self.refresh_snapshot()
        result = validate_listing_content(
            self.job,
            self.snapshot,
            ui_cfg=self.ui_cfg,
            title_value=title_value,
            price_value=price_value,
            description_value=description_value,
            media_count=media_count,
            expected_media_count=expected_media_count,
            location_value=location_value,
        )
        if not result.ok:
            self.safe_stop(
                f"validation_failed:{','.join(result.errors)}",
                publication_status="validation_failed",
            )
        return result

    def confirm_publish_allowed(self) -> None:
        if self.current_state == MarketplaceState.BLOCKED_CHECKPOINT:
            self.safe_stop("blocked_checkpoint", publication_status="blocked_checkpoint")
        if self.current_state == MarketplaceState.UNKNOWN_SCREEN:
            self.safe_stop("unknown_screen_before_publish", publication_status="needs_review")

    def verify_after_publish(self) -> tuple[bool, str]:
        ok, reason = verify_publish_pressed(self.refresh_snapshot, ui_cfg=self.ui_cfg)
        if ok:
            self.current_state = MarketplaceState.VERIFY_SUCCESS
            self.last_successful_state = MarketplaceState.VERIFY_SUCCESS
        return ok, reason

    def _try_vision_select(self, target: str, plan: ActionPlan | None = None) -> bool:
        vision_cfg = self.ui_cfg.get("vision", {})
        if not vision_cfg.get("enabled", False):
            return False
        if isinstance(self.vision, DisabledVisionProvider):
            return False
        elements = [
            {
                "id": node.node_id,
                "class": node.class_name,
                "text": node.text,
                "bounds": list(node.bounds or ()),
            }
            for node in self.snapshot.nodes
            if node.bounds
        ]
        request = VisionRequest(
            expected_state=self.current_state.value,
            allowed_actions=[
                f"select_{target}_field",
                "report_not_found",
                "report_wrong_screen",
            ],
            ui_elements=elements,
            forbidden_actions=["publish", "promote_boost", "coordinate_click"],
            target=target,
        )
        screenshot = None
        try:
            screenshot = self.device.screenshot(format="raw")
        except Exception:
            screenshot = None
        response = self.vision.select_element(
            screenshot=screenshot,
            snapshot=self.snapshot,
            request=request,
        )
        if self.vision_audit is not None:
            self.vision_audit.append(
                {"request": request.__dict__, "response": response.__dict__}
            )
        threshold = float(self.ui_cfg.get("confidence", {}).get("vision_execute", 0.92))
        if response.confidence < threshold:
            return False
        if response.action in request.forbidden_actions:
            return False
        if not response.selected_element_id:
            return False
        node = next(
            (item for item in self.snapshot.nodes if item.node_id == response.selected_element_id),
            None,
        )
        if node is None:
            return False
        point = bounds_center(node.bounds) if node.bounds else None
        if point and self._click_via_device(
            SelectorMatch(target, node, "vision", response.confidence, (response.reason,)),
            point,
            plan=plan
            or ActionPlan(
                self.current_state,
                "fill_field",
                target,
                "text",
                response.confidence,
                [response.reason],
            ),
        ):
            self.refresh_snapshot()
            return True
        return False


def session_from_xml(job: PublishJob, xml: str, publisher_cfg: dict[str, Any] | None = None) -> MarketplaceSession:
    """Offline helper for tests without a device."""
    class _OfflineDevice:
        def dump_hierarchy(self) -> str:
            return xml

        def screenshot(self, format: str = "raw"):  # noqa: A002
            return b""

        def app_current(self):
            return {"activity": "", "package": "com.facebook.katana"}

        def window_size(self):
            return 720, 1600

        def click(self, x: int, y: int) -> None:
            return None

        def __call__(self, **kwargs):
            return self

        def exists(self, timeout: float = 0) -> bool:
            return False

    ui_cfg = load_marketplace_ui_config(publisher_cfg or {})
    snapshot = snapshot_from_xml(xml)
    session = MarketplaceSession(
        device=_OfflineDevice(),
        job=job,
        ui_cfg=ui_cfg,
        actions_log=[],
        diagnostics=DiagnosticBundleWriter(ui_cfg),
        vision=load_vision_provider(ui_cfg),
        _snapshot=snapshot,
    )
    session.current_state = detect_marketplace_state(snapshot)
    return session
