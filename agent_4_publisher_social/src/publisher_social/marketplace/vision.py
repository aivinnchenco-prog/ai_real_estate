from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .snapshot import UISnapshot
from .states import MarketplaceState


@dataclass
class VisionRequest:
    expected_state: str
    allowed_actions: list[str]
    ui_elements: list[dict[str, Any]]
    forbidden_actions: list[str]
    target: str = ""


@dataclass
class VisionResponse:
    screen_state: str
    selected_element_id: str | None
    confidence: float
    reason: str
    action: str = "report_not_found"


class ScreenVisionProvider(Protocol):
    def classify_screen(
        self,
        *,
        screenshot: bytes | None,
        snapshot: UISnapshot,
        request: VisionRequest,
    ) -> VisionResponse: ...

    def select_element(
        self,
        *,
        screenshot: bytes | None,
        snapshot: UISnapshot,
        request: VisionRequest,
    ) -> VisionResponse: ...

    def verify_state(
        self,
        *,
        screenshot: bytes | None,
        snapshot: UISnapshot,
        request: VisionRequest,
    ) -> VisionResponse: ...


class DisabledVisionProvider:
    def classify_screen(self, **_: Any) -> VisionResponse:
        return VisionResponse(
            screen_state=MarketplaceState.UNKNOWN_SCREEN.value,
            selected_element_id=None,
            confidence=0.0,
            reason="vision_disabled",
        )

    def select_element(self, **_: Any) -> VisionResponse:
        return self.classify_screen()

    def verify_state(self, **_: Any) -> VisionResponse:
        return self.classify_screen()


class FakeVisionProvider:
    """Offline test provider with deterministic mapping."""

    def __init__(self, mapping: dict[str, VisionResponse] | None = None) -> None:
        self.mapping = mapping or {}
        self.audit: list[dict[str, Any]] = []

    def _respond(self, request: VisionRequest, method: str) -> VisionResponse:
        key = request.target or request.expected_state
        response = self.mapping.get(
            key,
            VisionResponse(
                screen_state=request.expected_state,
                selected_element_id=(
                    request.ui_elements[0]["id"] if request.ui_elements else None
                ),
                confidence=0.95,
                reason="fake_default",
                action=request.allowed_actions[0] if request.allowed_actions else "report_not_found",
            ),
        )
        self.audit.append(
            {
                "method": method,
                "request": asdict(request),
                "response": asdict(response),
            }
        )
        return response

    def classify_screen(self, *, request: VisionRequest, **_: Any) -> VisionResponse:
        return self._respond(request, "classify_screen")

    def select_element(self, *, request: VisionRequest, **_: Any) -> VisionResponse:
        return self._respond(request, "select_element")

    def verify_state(self, *, request: VisionRequest, **_: Any) -> VisionResponse:
        return self._respond(request, "verify_state")


def load_vision_provider(ui_cfg: dict[str, Any]) -> ScreenVisionProvider:
    vision_cfg = ui_cfg.get("vision", {})
    if not vision_cfg.get("enabled", False):
        return DisabledVisionProvider()
    provider_name = str(vision_cfg.get("provider") or "disabled")
    if provider_name == "fake":
        return FakeVisionProvider()
    return DisabledVisionProvider()


class VisionAuditLog:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, entry: dict[str, Any]) -> None:
        path = self.root / "vision_audit.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
