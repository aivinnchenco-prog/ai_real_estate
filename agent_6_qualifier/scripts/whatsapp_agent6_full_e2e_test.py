#!/usr/bin/env python3
"""Controlled WhatsApp Agent 6 FULL E2E watcher (does NOT invent client messages).

Arms stage-mode live flags for ONE allowlisted phone, watches webhook/session/amo
checkpoints, persists a safe transcript/report.

Usage (from agent_6_qualifier):
  PYTHONPATH=src:../contact_role/src python3 scripts/whatsapp_agent6_full_e2e_test.py arm
  PYTHONPATH=src:../contact_role/src python3 scripts/whatsapp_agent6_full_e2e_test.py status
  PYTHONPATH=src:../contact_role/src python3 scripts/whatsapp_agent6_full_e2e_test.py disarm

Does not simulate CLIENT/OWNER replies.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REPO = _ROOT.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
_CR = _REPO / "contact_role" / "src"
if str(_CR) not in sys.path:
    sys.path.insert(0, str(_CR))

TEST_PHONE_DEFAULT = "+66625124001"
REPORT_DIR = _ROOT / "data" / "test_reports"
STATE_PATH = REPORT_DIR / "whatsapp_agent6_full_e2e_state.json"
SESSIONS_DIR = _ROOT / "data" / "sessions"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _mask_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) <= 4:
        return "+****"
    return f"+{digits[:2]}******{digits[-3:]}"


def _upsert_env(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        raw = line.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            key = raw.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    missing = [k for k in updates if k not in seen]
    if missing:
        out.append("")
        out.append("# --- whatsapp_agent6_full_e2e_test.py ---")
        for key in missing:
            out.append(f"{key}={updates[key]}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _session_chat_id(phone: str) -> str:
    from agent6_qualifier.messaging.conversation_key import whatsapp_session_chat_id

    return whatsapp_session_chat_id(phone)


def _checkpoint_template() -> dict:
    names = [
        "inbound_received",
        "agent6_first_live_reply",
        "multi_turn_qualification",
        "qualification_complete",
        "amocrm_updated",
        "object_selection",
        "owner_check_triggered",
        "agent7_owner_outbound",
        "owner_reply_processed",
        "client_continuation",
        "booking_confirmed",
        "notary_contract_triggered",
    ]
    return {n: "PENDING" for n in names}


def cmd_prepare(phone: str) -> dict:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    chat_id = _session_chat_id(phone)
    sess_path = SESSIONS_DIR / f"{''.join(c for c in chat_id if c.isalnum() or c in '-_')}.json"
    snapshot = None
    if sess_path.exists():
        snap = REPORT_DIR / f"session_snapshot_{chat_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        shutil.copy2(sess_path, snap)
        snapshot = str(snap)
        sess_path.unlink()

    # Reset canonical role for this phone only (to CLIENT via inbound later).
    store_path = Path(
        os.environ.get("CONTACT_ROLE_STORE_PATH")
        or (_REPO / "contact_role" / "data" / "contact_roles_live_test.json")
    )
    role_reset = False
    if store_path.exists():
        from contact_role.roles import CanonicalRole
        from contact_role.sources import RoleSource
        from contact_role.state import ContactRoleState, ContactRoleStore

        store = ContactRoleStore(store_path)
        prev = store.get_by_phone(phone)
        meta = dict(prev.metadata) if prev else {}
        meta["contact_id"] = meta.get("contact_id") or 45601045
        meta["reset_for"] = "whatsapp_agent6_full_e2e"
        store.upsert(
            ContactRoleState(
                contact_key=f"wa:{phone}" if phone.startswith("+") else f"wa:+{phone}",
                phone=phone if phone.startswith("+") else f"+{phone}",
                canonical_role=CanonicalRole.UNKNOWN.value,
                role_source=RoleSource.UNKNOWN.value,
                role_set_at=_now(),
                role_updated_at=_now(),
                locked_by_manual_override=False,
                metadata=meta,
            )
        )
        role_reset = True

    state = {
        "armed": False,
        "test_phone": phone,
        "masked_phone": _mask_phone(phone),
        "session_chat_id": chat_id,
        "session_snapshot": snapshot,
        "role_reset": role_reset,
        "shared_core": True,
        "autoresponse_status": "NOT_RESOLVED_VIA_API",
        "autoresponse_source_doc": "Wazzup/WhatsApp Business greeting or amoCRM bot (README_WAZZUP.md)",
        "autoresponse_user_confirmed_off": False,
        "controlled_owner": None,
        "agent7_live": False,
        "checkpoints": _checkpoint_template(),
        "turns": [],
        "wazzup_sends": 0,
        "duplicate_sends": 0,
        "created_at": _now(),
        "updated_at": _now(),
        "current_checkpoint": "WAITING_PREPARE",
        "report_path": str(REPORT_DIR / f"whatsapp_agent6_full_e2e_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(state["report_path"]).write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return state


def cmd_arm(phone: str, *, autoresponse_confirmed: bool, owner_phone: str | None) -> dict:
    if not autoresponse_confirmed:
        raise SystemExit(
            "REFUSE ARM: confirm Wazzup/WhatsApp Business greeting autoresponse is OFF "
            "(pass --confirm-autoresponse-off). Source not readable via Wazzup API."
        )

    allow_phones = [phone]
    if owner_phone and owner_phone not in allow_phones:
        allow_phones.append(owner_phone)

    env_path = _ROOT / ".env"
    updates = {
        "WAZZUP_WEBHOOK_ENABLED": "true",
        "WAZZUP_SEND_ENABLED": "true",
        "WAZZUP_AUTO_REPLY_ENABLED": "true",
        "WAZZUP_LIVE_ALLOWLIST_ENABLED": "true",
        "WAZZUP_LIVE_ALLOWLIST_PHONES": ",".join(allow_phones),
        "WAZZUP_STAGE_MODE": "true",
        "AGENT7_LIVE_OUTREACH_ENABLED": "true" if owner_phone else "false",
        "AGENT7_CONTROLLED_OWNER_PHONES": owner_phone or "",
        "CONTACT_ROLE_AUTO_ASSIGN_ENABLED": "true",
        "CONTACT_ROLE_AMO_SYNC_ENABLED": "true",
        "CONTACT_ROLE_AMO_DRY_RUN": "false",
        "CONTACT_ROLE_ONE_SHOT_LIVE_TEST": "false",
        "WHATSAPP_UI_SYNC_ENABLED": "true",
        "WHATSAPP_UI_DRY_RUN": "false",
        "CONTACT_ROLE_STORE_PATH": str(
            (_REPO / "contact_role" / "data" / "contact_roles_live_test.json").resolve()
        ),
    }
    _upsert_env(env_path, updates)

    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    else:
        state = cmd_prepare(phone)

    state.update(
        {
            "armed": True,
            "armed_at": _now(),
            "updated_at": _now(),
            "autoresponse_user_confirmed_off": True,
            "autoresponse_status": "USER_CONFIRMED_OFF",
            "controlled_owner": owner_phone,
            "agent7_live": bool(owner_phone),
            "current_checkpoint": "WAITING FOR FIRST WHATSAPP MESSAGE",
            "flags": updates,
        }
    )
    if not owner_phone:
        state["checkpoints"]["agent7_owner_outbound"] = "BLOCKED_NEED_OWNER_LIVE_CONTACT"
        state["notes"] = [
            "Agent7 live suppressed until controlled owner contact is provided",
            "Qualification / amo / selection can proceed; STOP before real owner outbound",
        ]
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(state["report_path"]).write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return state


def cmd_disarm() -> dict:
    env_path = _ROOT / ".env"
    updates = {
        "WAZZUP_SEND_ENABLED": "false",
        "WAZZUP_AUTO_REPLY_ENABLED": "false",
        "WAZZUP_WEBHOOK_ENABLED": "false",
        "AGENT7_LIVE_OUTREACH_ENABLED": "false",
        "AGENT7_CONTROLLED_OWNER_PHONES": "",
        "CONTACT_ROLE_AUTO_ASSIGN_ENABLED": "false",
        "CONTACT_ROLE_AMO_SYNC_ENABLED": "false",
        "CONTACT_ROLE_AMO_DRY_RUN": "true",
        "WHATSAPP_UI_SYNC_ENABLED": "false",
        "WHATSAPP_UI_DRY_RUN": "true",
    }
    _upsert_env(env_path, updates)
    state = {}
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    state.update(
        {
            "armed": False,
            "disarmed_at": _now(),
            "updated_at": _now(),
            "flags_after": updates,
            "current_checkpoint": "DISARMED",
        }
    )
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def cmd_status() -> dict:
    if not STATE_PATH.exists():
        return {"armed": False, "error": "no state — run prepare/arm first"}
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    chat_id = state.get("session_chat_id") or ""
    safe = "".join(c for c in chat_id if c.isalnum() or c in "-_")
    sess_path = SESSIONS_DIR / f"{safe}.json"
    session = None
    if sess_path.exists():
        try:
            session = json.loads(sess_path.read_text(encoding="utf-8"))
        except Exception:
            session = {"error": "unreadable"}
    state["live_session_exists"] = sess_path.exists()
    if isinstance(session, dict):
        state["live_session_summary"] = {
            "asked_core": session.get("asked_core"),
            "amo_lead_id": session.get("amo_lead_id"),
            "awaiting_owner": session.get("awaiting_owner"),
            "owner_verdict": session.get("owner_verdict"),
            "booking_confirmed": session.get("booking_confirmed"),
            "handoff_to_human": session.get("handoff_to_human"),
            "preferred_object_id": (session.get("lead") or {}).get("preferred_object_id"),
            "history_len": len(session.get("history") or []),
        }
    return state


def main() -> int:
    _load_dotenv(_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "arm", "disarm", "status"])
    parser.add_argument("--phone", default=TEST_PHONE_DEFAULT)
    parser.add_argument(
        "--confirm-autoresponse-off",
        action="store_true",
        help="Required for arm: you confirmed greeting autoresponse is disabled",
    )
    parser.add_argument(
        "--owner-phone",
        default="",
        help="Controlled OWNER/AGENT test contact for Agent7 live step",
    )
    args = parser.parse_args()

    if args.command == "prepare":
        state = cmd_prepare(args.phone)
    elif args.command == "arm":
        state = cmd_arm(
            args.phone,
            autoresponse_confirmed=args.confirm_autoresponse_off,
            owner_phone=(args.owner_phone or None),
        )
    elif args.command == "disarm":
        state = cmd_disarm()
    else:
        state = cmd_status()

    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
