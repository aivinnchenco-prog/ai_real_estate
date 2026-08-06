from __future__ import annotations

import time
from typing import Any, Callable

from .detector import detect_marketplace_state
from .snapshot import UISnapshot
from .states import MarketplaceState
from .validator import FieldValidation, validate_field_for_target


def read_field_value(device, validation: FieldValidation) -> str:
    if validation.match is None:
        return ""
    node = validation.match.node
    if node.resource_id and device(resourceId=node.resource_id).exists(timeout=0.5):
        try:
            return str(device(resourceId=node.resource_id).get_text() or "").strip()
        except Exception:
            pass
    if device(focused=True).exists(timeout=0.3):
        try:
            return str(device(focused=True).get_text() or "").strip()
        except Exception:
            pass
    return node.text


def verify_field_value(
    device,
    snapshot: UISnapshot,
    *,
    target: str,
    expected: str,
    ui_cfg: dict[str, Any],
    state: MarketplaceState | None = None,
) -> tuple[bool, str]:
    validation = validate_field_for_target(snapshot, target, ui_cfg=ui_cfg, state=state)
    if not validation.ok:
        return False, "semantic_validation_failed"
    actual = read_field_value(device, validation)
    if not actual and target == "price":
        actual = read_field_value(device, validation)
    normalized_expected = (expected or "").strip()
    normalized_actual = (actual or "").strip()
    if normalized_expected and normalized_actual != normalized_expected:
        return False, f"value_mismatch expected={normalized_expected!r} actual={normalized_actual!r}"
    return True, "ok"


def wait_for_state_change(
    refresh_snapshot: Callable[[], UISnapshot],
    *,
    previous_state: MarketplaceState,
    timeout_seconds: float = 10.0,
    poll_seconds: float = 0.4,
) -> tuple[MarketplaceState, UISnapshot]:
    deadline = time.time() + timeout_seconds
    snapshot = refresh_snapshot()
    state = detect_marketplace_state(snapshot)
    while time.time() < deadline:
        if state != previous_state:
            return state, snapshot
        time.sleep(poll_seconds)
        snapshot = refresh_snapshot()
        state = detect_marketplace_state(snapshot)
    return state, snapshot


def verify_transition(
    refresh_snapshot: Callable[[], UISnapshot],
    *,
    previous_state: MarketplaceState,
    ui_cfg: dict[str, Any],
) -> tuple[bool, MarketplaceState, str]:
    timeouts = ui_cfg.get("timeouts", {})
    timeout = float(timeouts.get("ui_change_seconds", 10))
    new_state, _ = wait_for_state_change(
        refresh_snapshot,
        previous_state=previous_state,
        timeout_seconds=timeout,
    )
    if new_state == previous_state:
        return False, new_state, "state_unchanged"
    if new_state == MarketplaceState.UNKNOWN_SCREEN:
        return False, new_state, "unknown_after_transition"
    return True, new_state, "ok"


def verify_publish_pressed(
    refresh_snapshot: Callable[[], UISnapshot],
    *,
    ui_cfg: dict[str, Any],
) -> tuple[bool, str]:
    snapshot = refresh_snapshot()
    state = detect_marketplace_state(snapshot)
    if state == MarketplaceState.PUBLISH_CONFIRMATION:
        return False, "still_on_publish_screen"
    if state in {MarketplaceState.BLOCKED_CHECKPOINT, MarketplaceState.UNKNOWN_SCREEN}:
        return False, state.value
    if snapshot.text_present(
        "Опубликовано",
        "Published",
        "объявление опубликовано",
        "listing published",
        "Marketplace",
    ):
        return True, "success_signals_present"
    # Composer gone after publish is weak but acceptable signal.
    if not snapshot.text_present("Новое объявление", "New listing", "Опубликовать", "Publish"):
        return True, "left_composer_or_publish"
    return False, "publish_not_confirmed"
