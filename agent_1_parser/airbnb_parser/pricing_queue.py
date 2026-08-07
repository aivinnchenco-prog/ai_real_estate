"""Persistent fair queue для background monthly pricing."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from CustomLogger import logger
from monthly_pricing import entry_has_price
from pricing_config import queue_path, stale_running_seconds
from pricing_state import ACTIVE_JOB_STATUSES, TERMINAL_JOB_STATUSES

_LOCK = threading.Lock()
_OBJECT_SEQ = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_id(object_id: str, month: str) -> str:
    return f"{object_id}:{month}"


class PricingQueue:
    """JSON-backed queue: один месяц = одна задача, fair pick по month_depth."""

    def __init__(self, path: Path | None = None):
        self.path = path or queue_path()
        self._data: dict[str, Any] = {"jobs": [], "objects": {}, "meta": {"object_seq": 0}}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"pricing queue read failed: {exc}")
            return
        self._data.setdefault("jobs", [])
        self._data.setdefault("objects", {})
        self._data.setdefault("meta", {})
        self._recover_stale_running()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _recover_stale_running(self) -> None:
        cutoff = time.time() - stale_running_seconds()
        changed = False
        for job in self._data["jobs"]:
            if job.get("status") != "running":
                continue
            updated = job.get("updated_at_ts") or 0
            if updated and updated < cutoff:
                job["status"] = "retry"
                job["updated_at"] = _utc_now()
                changed = True
        if changed:
            self.save()

    def next_object_seq(self) -> int:
        seq = int(self._data["meta"].get("object_seq", 0)) + 1
        self._data["meta"]["object_seq"] = seq
        return seq

    def get_object(self, object_id: str) -> dict[str, Any] | None:
        return self._data["objects"].get(object_id)

    def upsert_object(
        self,
        object_id: str,
        *,
        listing_url: str,
        monthly_prices: dict | None = None,
        calendar: dict[str, bool] | None = None,
        session_id: str = "",
        notion_page_id: str = "",
        months_target: int = 12,
        object_seq: int | None = None,
    ) -> dict[str, Any]:
        obj = dict(self._data["objects"].get(object_id) or {})
        obj["object_id"] = object_id
        obj["listing_url"] = listing_url
        if object_seq is not None:
            obj["object_seq"] = object_seq
        elif "object_seq" not in obj:
            obj["object_seq"] = self.next_object_seq()
        if session_id:
            obj["session_id"] = session_id
        if notion_page_id:
            obj["notion_page_id"] = notion_page_id
        if calendar is not None:
            obj["calendar"] = calendar
        if monthly_prices is not None:
            merged = dict(obj.get("monthly_prices") or {})
            for key, entry in monthly_prices.items():
                if entry_has_price(entry) or key not in merged:
                    merged[key] = entry
                elif entry_has_price(merged.get(key)):
                    pass
                else:
                    merged[key] = entry
            obj["monthly_prices"] = merged
        obj["pricing_months_target"] = months_target
        obj["block_streak"] = int(obj.get("block_streak") or 0)
        obj["last_served_at"] = float(obj.get("last_served_at") or 0)
        self._data["objects"][object_id] = obj
        return obj

    def save_month_result(
        self,
        object_id: str,
        month: str,
        entry: dict,
    ) -> dict[str, Any]:
        obj = self.upsert_object(object_id, listing_url=self._data["objects"][object_id]["listing_url"])
        monthly = dict(obj.get("monthly_prices") or {})
        monthly[month] = entry
        obj["monthly_prices"] = monthly
        self._data["objects"][object_id] = obj
        return obj

    def enqueue_background_months(
        self,
        object_id: str,
        listing_url: str,
        months: list[str],
        *,
        existing: dict | None = None,
        object_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        """Создаёт задачи для months[0]=depth1, months[1]=depth2, …; skip done."""
        existing = existing or {}
        obj = self.upsert_object(
            object_id,
            listing_url=listing_url,
            object_seq=object_seq,
        )
        seq = int(obj["object_seq"])
        known = {j["job_id"]: j for j in self._data["jobs"]}
        created: list[dict[str, Any]] = []
        for depth, month in enumerate(months, start=1):
            if entry_has_price(existing.get(month)):
                continue
            jid = _job_id(object_id, month)
            if jid in known and known[jid].get("status") in TERMINAL_JOB_STATUSES | {"done"}:
                continue
            if jid in known:
                job = known[jid]
                if job.get("status") not in TERMINAL_JOB_STATUSES:
                    created.append(job)
                continue
            job = {
                "job_id": jid,
                "object_id": object_id,
                "listing_url": listing_url,
                "month": month,
                "month_depth": depth,
                "status": "pending",
                "attempt": 0,
                "next_retry_at": None,
                "object_seq": seq,
                "updated_at": _utc_now(),
                "updated_at_ts": time.time(),
            }
            self._data["jobs"].append(job)
            created.append(job)
        return created

    def _eligible_jobs(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now if now is not None else time.time()
        out = []
        for job in self._data["jobs"]:
            status = job.get("status")
            if status == "blocked":
                nra = job.get("next_retry_at")
                if nra is not None and now < float(nra):
                    continue
                job["status"] = "retry"
            if status not in ("pending", "retry"):
                continue
            nra = job.get("next_retry_at")
            if nra is not None and now < float(nra):
                continue
            out.append(job)
        return out

    def pick_next_job(self, now: float | None = None) -> dict[str, Any] | None:
        """Fair: min month_depth, then oldest last_served_at на объекте."""
        eligible = self._eligible_jobs(now)
        if not eligible:
            return None
        min_depth = min(int(j.get("month_depth") or 1) for j in eligible)
        candidates = [j for j in eligible if int(j.get("month_depth") or 1) == min_depth]

        def sort_key(job: dict) -> tuple[float, int]:
            obj = self._data["objects"].get(job["object_id"]) or {}
            return (float(obj.get("last_served_at") or 0), int(job.get("object_seq") or 0))

        return min(candidates, key=sort_key)

    def mark_running(self, job: dict[str, Any]) -> None:
        job["status"] = "running"
        job["updated_at"] = _utc_now()
        job["updated_at_ts"] = time.time()

    def mark_done(self, job: dict[str, Any], terminal_status: str = "done") -> None:
        job["status"] = terminal_status
        job["updated_at"] = _utc_now()
        job["updated_at_ts"] = time.time()
        obj = self._data["objects"].get(job["object_id"])
        if obj is not None:
            obj["last_served_at"] = time.time()

    def mark_retry(
        self,
        job: dict[str, Any],
        *,
        delay_sec: float,
        error: str = "",
        as_blocked: bool = False,
    ) -> None:
        job["attempt"] = int(job.get("attempt") or 0) + 1
        job["status"] = "blocked" if as_blocked else "retry"
        job["next_retry_at"] = time.time() + delay_sec
        job["error"] = (error or "")[:500]
        job["updated_at"] = _utc_now()
        job["updated_at_ts"] = time.time()

    def has_active_jobs(self, object_id: str | None = None) -> bool:
        for job in self._data["jobs"]:
            if object_id and job.get("object_id") != object_id:
                continue
            if job.get("status") in ACTIVE_JOB_STATUSES:
                return True
        return False

    def jobs_for_object(self, object_id: str) -> list[dict[str, Any]]:
        return [j for j in self._data["jobs"] if j.get("object_id") == object_id]


def get_queue() -> PricingQueue:
    with _LOCK:
        return PricingQueue()
