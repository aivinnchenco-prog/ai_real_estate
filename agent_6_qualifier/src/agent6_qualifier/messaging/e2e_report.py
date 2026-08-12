"""Safe E2E transcript/checkpoint reporter for WhatsApp full live test."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_STATE = _ROOT / "data" / "test_reports" / "whatsapp_agent6_full_e2e_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_path() -> Path:
    return Path(os.getenv("WA_E2E_STATE_PATH") or _DEFAULT_STATE)


def _mask_phone(phone: str) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit())
    if len(digits) <= 4:
        return "+****"
    return f"+{digits[:2]}******{digits[-3:]}"


def _load() -> dict[str, Any] | None:
    path = _state_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = state.get("report_path")
    if report:
        Path(report).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def _set_checkpoint(state: dict[str, Any], name: str, value: str) -> None:
    cps = state.setdefault("checkpoints", {})
    prev = cps.get(name)
    if prev in {"PASS", "FAIL", "BLOCKED"} and value == "PASS" and prev != "PASS":
        cps[name] = value
    elif prev not in {"PASS", "FAIL", "BLOCKED"}:
        cps[name] = value
    elif value in {"FAIL", "BLOCKED"}:
        cps[name] = value
    else:
        cps[name] = value


def record_client_turn(
    *,
    inbound_text: str,
    phone: str | None,
    turn: Any,
    session_summary: dict[str, Any] | None = None,
) -> None:
    state = _load()
    if not state or not state.get("armed"):
        return
    turns = state.setdefault("turns", [])
    turn_no = len(turns) + 1
    outbound_mode = getattr(turn, "outbound_mode", "")
    replied = bool(getattr(turn, "replied", False))
    live = outbound_mode == "live"

    turns.append(
        {
            "turn": turn_no,
            "sender": "CLIENT",
            "timestamp": _now(),
            "masked_identity": _mask_phone(phone or ""),
            "message_type": "text",
            "text_summary": (inbound_text or "")[:240],
            "agent6_reply_summary": (getattr(turn, "reply_preview", "") or "")[:240],
            "outbound_mode": outbound_mode,
            "qualification": session_summary or {},
            "amo_lead_id": getattr(turn, "amo_lead_id", None),
            "events": [
                e
                for e in [
                    "inbound",
                    "agent6_reply" if replied else "",
                    "need_owner_check" if getattr(turn, "need_owner_check", False) else "",
                    "booking_confirmed" if getattr(turn, "booking_confirmed", False) else "",
                    getattr(turn, "owner_check_status", "") or "",
                ]
                if e
            ],
        }
    )
    if replied and live:
        turns.append(
            {
                "turn": turn_no,
                "sender": "AGENT6",
                "timestamp": _now(),
                "masked_identity": "agent6",
                "message_type": "text",
                "text_summary": (getattr(turn, "reply_preview", "") or "")[:240],
                "outbound_mode": "live",
            }
        )
        state["wazzup_sends"] = int(state.get("wazzup_sends") or 0) + 1

    _set_checkpoint(state, "inbound_received", "PASS")
    if live and replied:
        _set_checkpoint(state, "agent6_first_live_reply", "PASS")
    if turn_no >= 2 and replied:
        _set_checkpoint(state, "multi_turn_qualification", "PASS")
    if session_summary and session_summary.get("qualification_complete"):
        _set_checkpoint(state, "qualification_complete", "PASS")
    if getattr(turn, "amo_lead_id", None):
        _set_checkpoint(state, "amocrm_updated", "PASS")
    if session_summary and session_summary.get("preferred_object_id"):
        _set_checkpoint(state, "object_selection", "PASS")
    if getattr(turn, "need_owner_check", False):
        status = getattr(turn, "owner_check_status", "") or ""
        if "SUPPRESSED" in status or "BLOCKED" in status:
            _set_checkpoint(state, "owner_check_triggered", "BLOCKED")
            _set_checkpoint(state, "agent7_owner_outbound", "BLOCKED")
            state["current_checkpoint"] = "BLOCKED AT OWNER LIVE CONTACT"
        else:
            _set_checkpoint(state, "owner_check_triggered", "PASS")
    if getattr(turn, "booking_confirmed", False):
        _set_checkpoint(state, "booking_confirmed", "PASS")
        # Notary path is triggered from shared core; mark triggered if note present.
        notes = getattr(turn, "notes", None) or []
        if any("notary" in str(n).lower() or "booking" in str(n).lower() for n in notes):
            _set_checkpoint(state, "notary_contract_triggered", "PASS")
            state["notary_mode"] = "TRIGGERED BUT EXTERNAL WRITE MAY BE DEFERRED"
    state["current_checkpoint"] = state.get("current_checkpoint") or f"TURN_{turn_no}"
    _save(state)


def record_owner_turn(*, turn: Any, phone: str | None) -> None:
    state = _load()
    if not state or not state.get("armed"):
        return
    turns = state.setdefault("turns", [])
    turns.append(
        {
            "turn": len(turns) + 1,
            "sender": "OWNER",
            "timestamp": _now(),
            "masked_identity": _mask_phone(phone or ""),
            "message_type": "text",
            "text_summary": (getattr(turn, "owner_reply_preview", "") or "")[:240],
            "events": ["owner_reply_processed"] if getattr(turn, "processed", False) else [],
        }
    )
    if getattr(turn, "processed", False):
        _set_checkpoint(state, "owner_reply_processed", "PASS")
    if getattr(turn, "client_notified", False):
        _set_checkpoint(state, "client_continuation", "PASS")
        state["wazzup_sends"] = int(state.get("wazzup_sends") or 0) + 1
    _save(state)


def record_agent7_outbound(*, object_id: str, owner_masked: str) -> None:
    state = _load()
    if not state or not state.get("armed"):
        return
    turns = state.setdefault("turns", [])
    turns.append(
        {
            "turn": len(turns) + 1,
            "sender": "AGENT7",
            "timestamp": _now(),
            "masked_identity": owner_masked,
            "message_type": "text",
            "text_summary": f"owner availability check object={object_id}",
            "events": ["agent7_owner_outbound"],
        }
    )
    _set_checkpoint(state, "owner_check_triggered", "PASS")
    _set_checkpoint(state, "agent7_owner_outbound", "PASS")
    state["wazzup_sends"] = int(state.get("wazzup_sends") or 0) + 1
    state["current_checkpoint"] = "WAITING FOR OWNER REPLY"
    _save(state)
