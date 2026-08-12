"""One-shot live guard for the first real Agent7 OWNER/AGENT outreach.

Arming (manual, not done by this module):
  CONTACT_ROLE_AUTO_ASSIGN_ENABLED=true
  CONTACT_ROLE_ONE_SHOT_LIVE_TEST=true
  (+ mirrors if desired)

Only ONE new qualifying Agent7 outreach event is accepted after activation.
Consumed state is persisted to disk (env is not mutated at runtime).
Event-driven only — never scans existing contacts or queues.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .flags import auto_assign_enabled, one_shot_live_test_enabled
from .phone import mask_phone
from .roles import CanonicalRole


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def default_one_shot_state_path() -> Path:
    override = (os.getenv("CONTACT_ROLE_ONE_SHOT_STATE_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "one_shot_live_state.json"


def default_one_shot_report_path() -> Path:
    override = (os.getenv("CONTACT_ROLE_ONE_SHOT_REPORT_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "one_shot_live_event_report.json"


VINCHENCO_CONTACT_ID = 45601045
VINCHENCO_PHONE = "+66625124001"


@dataclass
class OneShotState:
    activated_at: str = ""
    consumed: bool = False
    consumed_at: str = ""
    event_id: str = ""
    contact_key: str = ""
    masked_phone: str = ""
    role: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OneShotState:
        return cls(
            activated_at=str(data.get("activated_at") or ""),
            consumed=bool(data.get("consumed")),
            consumed_at=str(data.get("consumed_at") or ""),
            event_id=str(data.get("event_id") or ""),
            contact_key=str(data.get("contact_key") or ""),
            masked_phone=str(data.get("masked_phone") or ""),
            role=str(data.get("role") or ""),
            notes=list(data.get("notes") or []),
        )


@dataclass
class OneShotGateResult:
    allowed: bool
    reason: str
    state: OneShotState
    event_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "event_id": self.event_id,
            "activated_at": self.state.activated_at,
            "consumed": self.state.consumed,
        }


@dataclass
class OneShotReport:
    event_id: str
    timestamp: str
    contact_id: int | None
    masked_phone: str
    role: str
    role_source: str
    trigger: str
    role_assigned_before_outbound: bool
    routing: str
    amo_status: str
    wa_status: str
    outbound_status: str
    one_shot_consumed: bool
    contact_key: str = ""
    old_role: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OneShotLiveGuard:
    """Activation watermark + single-consume guard (event-driven only)."""

    def __init__(
        self,
        state_path: Path | None = None,
        report_path: Path | None = None,
    ):
        self.state_path = state_path or default_one_shot_state_path()
        self.report_path = report_path or default_one_shot_report_path()
        self._lock = threading.Lock()

    def load(self) -> OneShotState:
        if not self.state_path.exists():
            return OneShotState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return OneShotState()
        if not isinstance(data, dict):
            return OneShotState()
        return OneShotState.from_dict(data)

    def save(self, state: OneShotState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def ensure_activated(self) -> OneShotState:
        """Set activation watermark on first arm (when flags are on)."""
        with self._lock:
            state = self.load()
            if state.activated_at:
                return state
            state.activated_at = _now()
            state.notes.append("activated")
            self.save(state)
            return state

    def reset_for_tests(self) -> None:
        with self._lock:
            if self.state_path.exists():
                self.state_path.unlink()
            if self.report_path.exists():
                self.report_path.unlink()

    def evaluate_agent7_event(
        self,
        *,
        role: CanonicalRole | str,
        event_at: str | None = None,
        force: bool = False,
    ) -> OneShotGateResult:
        """Decide whether this Agent7 outreach may consume the one-shot slot."""
        role_n = CanonicalRole.parse(role)
        event_id = str(uuid.uuid4())

        if force:
            return OneShotGateResult(
                allowed=True, reason="force", state=self.load(), event_id=event_id
            )

        if not auto_assign_enabled():
            return OneShotGateResult(
                allowed=False,
                reason="auto_assign_disabled",
                state=self.load(),
                event_id=event_id,
            )

        if not one_shot_live_test_enabled():
            # Full auto mode (not one-shot) — allow all Agent7 events.
            return OneShotGateResult(
                allowed=True,
                reason="auto_assign_open",
                state=self.load(),
                event_id=event_id,
            )

        if role_n not in {CanonicalRole.OWNER, CanonicalRole.AGENT}:
            return OneShotGateResult(
                allowed=False,
                reason="role_not_eligible",
                state=self.load(),
                event_id=event_id,
            )

        state = self.ensure_activated()
        if state.consumed:
            return OneShotGateResult(
                allowed=False,
                reason="one_shot_consumed",
                state=state,
                event_id=event_id,
            )

        activated = _parse_ts(state.activated_at)
        event_ts = _parse_ts(event_at) or datetime.now(timezone.utc)
        if activated is not None:
            # Normalize aware/naive comparison
            if activated.tzinfo is None:
                activated = activated.replace(tzinfo=timezone.utc)
            if event_ts.tzinfo is None:
                event_ts = event_ts.replace(tzinfo=timezone.utc)
            if event_ts < activated:
                return OneShotGateResult(
                    allowed=False,
                    reason="event_before_activation",
                    state=state,
                    event_id=event_id,
                )

        return OneShotGateResult(
            allowed=True,
            reason="one_shot_armed",
            state=state,
            event_id=event_id,
        )

    def consume(
        self,
        *,
        event_id: str,
        contact_key: str,
        phone: str | None,
        role: str,
        role_source: str,
        trigger: str,
        routing: str,
        old_role: str = "",
        contact_id: int | None = None,
        amo_status: str = "",
        wa_status: str = "",
        outbound_status: str = "NOT_SENT",
        role_assigned_before_outbound: bool = True,
        notes: list[str] | None = None,
    ) -> OneShotReport:
        with self._lock:
            state = self.load()
            if not state.activated_at:
                state.activated_at = _now()
            state.consumed = True
            state.consumed_at = _now()
            state.event_id = event_id
            state.contact_key = contact_key
            state.masked_phone = mask_phone(phone)
            state.role = role
            state.notes.append("consumed")
            self.save(state)

            report = OneShotReport(
                event_id=event_id,
                timestamp=state.consumed_at,
                contact_id=contact_id,
                masked_phone=state.masked_phone,
                role=role,
                role_source=role_source,
                trigger=trigger,
                role_assigned_before_outbound=role_assigned_before_outbound,
                routing=routing,
                amo_status=amo_status or "UNKNOWN",
                wa_status=wa_status or "UNKNOWN",
                outbound_status=outbound_status,
                one_shot_consumed=True,
                contact_key=contact_key,
                old_role=old_role,
                notes=list(notes or []),
            )
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            self.report_path.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return report


def live_auto_assign_gate(
    *,
    force: bool = False,
    agent7_outreach: bool = False,
) -> tuple[bool, str]:
    """Shared gate for Agent6/7 hooks.

    One-shot mode: only Agent7 OWNER/AGENT path may proceed (until consumed).
    """
    if force:
        return True, "force"
    if not auto_assign_enabled():
        return False, "auto_assign_disabled"
    if not one_shot_live_test_enabled():
        return True, "auto_assign_open"
    # One-shot armed
    guard = OneShotLiveGuard()
    state = guard.load()
    if state.consumed:
        return False, "one_shot_consumed"
    if not agent7_outreach:
        return False, "one_shot_agent7_only"
    return True, "one_shot_armed"
