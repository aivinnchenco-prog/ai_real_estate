"""Conservative rate guard for Airbnb owner messaging."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


@dataclass
class RateGuardDecision:
    allowed: bool
    reason: str = ""
    hour_count: int = 0
    day_count: int = 0
    max_per_hour: int = 0
    max_per_day: int = 0


class AirbnbOwnerMessageRateGuard:
    """Blocks uncontrolled bulk Airbnb host messaging."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        max_per_hour: int | None = None,
        max_per_day: int | None = None,
    ):
        self.max_per_hour = (
            max_per_hour
            if max_per_hour is not None
            else _parse_int("AIRBNB_OWNER_MESSAGES_MAX_PER_HOUR", 3)
        )
        self.max_per_day = (
            max_per_day
            if max_per_day is not None
            else _parse_int("AIRBNB_OWNER_MESSAGES_MAX_PER_DAY", 12)
        )
        if path is None:
            override = (os.getenv("AIRBNB_OWNER_RATE_GUARD_PATH") or "").strip()
            if override:
                path = Path(override).expanduser()
            else:
                path = (
                    Path(__file__).resolve().parents[3]
                    / "data"
                    / "airbnb_owner_rate_guard.json"
                )
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        stamps = data.get("sent_at") if isinstance(data, dict) else None
        if not isinstance(stamps, list):
            return []
        return [str(x) for x in stamps]

    def _write(self, stamps: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"sent_at": stamps}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def _prune(self, stamps: list[str]) -> list[str]:
        cutoff = _now() - timedelta(days=2)
        kept: list[str] = []
        for raw in stamps:
            try:
                ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts >= cutoff:
                kept.append(ts.astimezone(timezone.utc).isoformat(timespec="seconds"))
        return kept

    def check(self) -> RateGuardDecision:
        with self._lock:
            stamps = self._prune(self._read())
        now = _now()
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)
        hour_count = 0
        day_count = 0
        for raw in stamps:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if ts >= day_ago:
                day_count += 1
            if ts >= hour_ago:
                hour_count += 1
        if hour_count >= self.max_per_hour:
            return RateGuardDecision(
                allowed=False,
                reason="AIRBNB_RATE_LIMIT_GUARD",
                hour_count=hour_count,
                day_count=day_count,
                max_per_hour=self.max_per_hour,
                max_per_day=self.max_per_day,
            )
        if day_count >= self.max_per_day:
            return RateGuardDecision(
                allowed=False,
                reason="AIRBNB_RATE_LIMIT_GUARD",
                hour_count=hour_count,
                day_count=day_count,
                max_per_hour=self.max_per_hour,
                max_per_day=self.max_per_day,
            )
        return RateGuardDecision(
            allowed=True,
            hour_count=hour_count,
            day_count=day_count,
            max_per_hour=self.max_per_hour,
            max_per_day=self.max_per_day,
        )

    def record_send(self) -> None:
        with self._lock:
            stamps = self._prune(self._read())
            stamps.append(_now().isoformat(timespec="seconds"))
            self._write(stamps)
