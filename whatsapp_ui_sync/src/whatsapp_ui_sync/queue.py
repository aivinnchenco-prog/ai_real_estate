"""Lightweight local outbox for WhatsApp list sync jobs.

Qualification never waits on this queue. Browser sync is optional and async.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .config import WhatsAppUiSyncConfig, default_queue_dir
from .phone import normalize_phone_e164
from .roles import CanonicalRole, map_role_to_list, role_changed


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class RoleSyncJob:
    job_id: str
    phone: str
    canonical_role: str
    target_list: str | None
    created_at: str
    status: str = JobStatus.PENDING.value
    attempt_count: int = 0
    last_error: str = ""
    updated_at: str = ""
    result_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoleSyncJob:
        return cls(
            job_id=str(data.get("job_id") or ""),
            phone=str(data.get("phone") or ""),
            canonical_role=str(data.get("canonical_role") or ""),
            target_list=data.get("target_list"),
            created_at=str(data.get("created_at") or ""),
            status=str(data.get("status") or JobStatus.PENDING.value),
            attempt_count=int(data.get("attempt_count") or 0),
            last_error=str(data.get("last_error") or ""),
            updated_at=str(data.get("updated_at") or ""),
            result_code=str(data.get("result_code") or ""),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RoleSyncQueue:
    """JSON file outbox: role_sync_jobs."""

    def __init__(self, path: Path | None = None, *, max_attempts: int = 3):
        self.path = path or (default_queue_dir() / "role_sync_jobs.json")
        self.max_attempts = max_attempts
        self._lock = threading.Lock()

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            return list(data["jobs"])
        if isinstance(data, list):
            return data
        return []

    def _write(self, jobs: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"jobs": jobs}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def list_jobs(self) -> list[RoleSyncJob]:
        with self._lock:
            return [RoleSyncJob.from_dict(j) for j in self._read()]

    def find_open_job(self, phone: str, role: str) -> RoleSyncJob | None:
        phone_n = normalize_phone_e164(phone) or phone
        role_n = CanonicalRole.parse(role).value
        for job in self.list_jobs():
            if job.phone == phone_n and job.canonical_role == role_n:
                if job.status in {JobStatus.PENDING.value, JobStatus.RUNNING.value}:
                    return job
        return None

    def enqueue(
        self,
        phone: str,
        role: CanonicalRole | str,
        *,
        previous_role: CanonicalRole | str | None = None,
        config: WhatsAppUiSyncConfig | None = None,
        force: bool = False,
    ) -> RoleSyncJob | None:
        """Enqueue when role first known or changed. Dedupes open jobs.

        Returns None when no job should be created (UNKNOWN / unchanged / duplicate).
        """
        cfg = config or WhatsAppUiSyncConfig.from_env()
        phone_n = normalize_phone_e164(phone)
        if not phone_n:
            return None
        canonical = CanonicalRole.parse(role)
        if canonical is CanonicalRole.UNKNOWN:
            return None
        if not force and previous_role is not None and not role_changed(previous_role, canonical):
            return None

        existing = self.find_open_job(phone_n, canonical.value)
        if existing and not force:
            return existing

        target = map_role_to_list(
            canonical,
            client_list=cfg.client_list_name,
            owner_list=cfg.owner_list_name,
            agent_list=cfg.agent_list_name,
        )
        job = RoleSyncJob(
            job_id=str(uuid.uuid4()),
            phone=phone_n,
            canonical_role=canonical.value,
            target_list=target,
            created_at=_now(),
            updated_at=_now(),
            status=JobStatus.PENDING.value,
        )
        with self._lock:
            jobs = self._read()
            jobs.append(job.to_dict())
            self._write(jobs)
        return job

    def mark(
        self,
        job_id: str,
        *,
        status: JobStatus | str,
        last_error: str = "",
        result_code: str = "",
        bump_attempt: bool = False,
    ) -> RoleSyncJob | None:
        status_v = status.value if isinstance(status, JobStatus) else str(status)
        with self._lock:
            jobs = self._read()
            found: RoleSyncJob | None = None
            for i, raw in enumerate(jobs):
                if str(raw.get("job_id")) != job_id:
                    continue
                job = RoleSyncJob.from_dict(raw)
                if bump_attempt:
                    job.attempt_count += 1
                job.status = status_v
                job.last_error = last_error
                if result_code:
                    job.result_code = result_code
                job.updated_at = _now()
                jobs[i] = job.to_dict()
                found = job
                break
            if found is not None:
                self._write(jobs)
            return found

    def pending(self) -> list[RoleSyncJob]:
        return [j for j in self.list_jobs() if j.status == JobStatus.PENDING.value]

    def should_retry(self, job: RoleSyncJob) -> bool:
        return (
            job.status in {JobStatus.PENDING.value, JobStatus.FAILED.value}
            and job.attempt_count < self.max_attempts
        )
