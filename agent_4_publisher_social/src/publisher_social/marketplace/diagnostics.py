from __future__ import annotations

import json
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..dotenv_util import package_root
from .config import redact_secrets
from .guard import ActionPlan
from .states import MarketplaceState


@dataclass
class DiagnosticContext:
    listing_id: str
    publication_job_id: str
    timestamp: str
    current_state: str
    last_successful_state: str | None = None
    planned_action: dict[str, Any] | None = None
    confidence: float | None = None
    reason: str = ""
    activity: str = ""
    screen_resolution: str = ""
    app_version: str = ""
    exception: str = ""
    ui_variant: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class DiagnosticBundleWriter:
    def __init__(self, ui_cfg: dict[str, Any]) -> None:
        diag_cfg = ui_cfg.get("diagnostics", {})
        self.enabled = bool(diag_cfg.get("enabled", True))
        self.save_screenshot = bool(diag_cfg.get("save_screenshot", True))
        self.save_ui_dump = bool(diag_cfg.get("save_ui_dump", True))
        root = str(diag_cfg.get("root_dir") or "diagnostics")
        self.root = package_root() / root

    def save(
        self,
        *,
        context: DiagnosticContext,
        xml: str = "",
        screenshot_bytes: bytes | None = None,
        actions: list[dict[str, Any]] | None = None,
    ) -> Path | None:
        if not self.enabled:
            return None
        safe_ts = context.timestamp.replace(":", "-")
        bundle_dir = self.root / context.listing_id / safe_ts
        bundle_dir.mkdir(parents=True, exist_ok=True)

        context_payload = asdict(context)
        context_path = bundle_dir / "context.json"
        context_path.write_text(
            redact_secrets(json.dumps(context_payload, ensure_ascii=False, indent=2)),
            encoding="utf-8",
        )

        if actions:
            actions_path = bundle_dir / "actions.jsonl"
            lines = [
                redact_secrets(json.dumps(item, ensure_ascii=False))
                for item in actions
            ]
            actions_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        if context.exception:
            (bundle_dir / "error.txt").write_text(
                redact_secrets(context.exception),
                encoding="utf-8",
            )

        if self.save_ui_dump and xml:
            (bundle_dir / "ui_dump.xml").write_text(redact_secrets(xml), encoding="utf-8")

        if self.save_screenshot and screenshot_bytes:
            (bundle_dir / "screenshot.png").write_bytes(screenshot_bytes)

        return bundle_dir


def build_context(
    *,
    listing_id: str,
    publication_job_id: str,
    current_state: MarketplaceState,
    last_successful_state: MarketplaceState | None = None,
    plan: ActionPlan | None = None,
    reason: str = "",
    activity: str = "",
    resolution: tuple[int, int] | None = None,
    app_version: str = "",
    exc: BaseException | None = None,
    ui_variant: str | None = None,
) -> DiagnosticContext:
    return DiagnosticContext(
        listing_id=listing_id,
        publication_job_id=publication_job_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        current_state=current_state.value,
        last_successful_state=(
            last_successful_state.value if last_successful_state else None
        ),
        planned_action=plan.to_dict() if plan else None,
        confidence=plan.confidence if plan else None,
        reason=reason,
        activity=activity,
        screen_resolution=(
            f"{resolution[0]}x{resolution[1]}" if resolution else ""
        ),
        app_version=app_version,
        exception="".join(traceback.format_exception(exc)) if exc else "",
        ui_variant=ui_variant,
    )
