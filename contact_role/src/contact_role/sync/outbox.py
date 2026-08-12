"""Unified contact_role_sync_jobs outbox (AMOCRM + WHATSAPP_UI targets)."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..phone import normalize_phone_e164
from ..roles import CanonicalRole


class SyncTarget(str, Enum):
    AMOCRM = "AMOCRM"
    WHATSAPP_UI = "WHATSAPP_UI"


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ContactRoleSyncJob:
    job_id: str
    contact_key: str
    phone: str
    contact_id: int | None
    role: str
    role_source: str
    target: str
    created_at: str
    status: str = JobStatus.PENDING.value
    attempt_count: int = 0
    last_error: str = ""
    result_code: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContactRoleSyncJob:
        cid = data.get("contact_id")
        return cls(
            job_id=str(data.get("job_id") or ""),
            contact_key=str(data.get("contact_key") or ""),
            phone=str(data.get("phone") or ""),
            contact_id=int(cid) if cid is not None else None,
            role=str(data.get("role") or ""),
            role_source=str(data.get("role_source") or ""),
            target=str(data.get("target") or ""),
            created_at=str(data.get("created_at") or ""),
            status=str(data.get("status") or JobStatus.PENDING.value),
            attempt_count=int(data.get("attempt_count") or 0),
            last_error=str(data.get("last_error") or ""),
            result_code=str(data.get("result_code") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


class ContactRoleSyncOutbox:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._done_keys: set[tuple[str, str, str]] = set()

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
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def list_jobs(self) -> list[ContactRoleSyncJob]:
        with self._lock:
            return [ContactRoleSyncJob.from_dict(j) for j in self._read()]

    def find_open(
        self, *, contact_key: str, role: str, target: str
    ) -> ContactRoleSyncJob | None:
        role_n = CanonicalRole.parse(role).value
        for job in self.list_jobs():
            if (
                job.contact_key == contact_key
                and job.role == role_n
                and job.target == target
                and job.status
                in {JobStatus.PENDING.value, JobStatus.RUNNING.value}
            ):
                return job
        return None

    def enqueue(
        self,
        *,
        contact_key: str,
        phone: str | None,
        role: CanonicalRole | str,
        role_source: str,
        target: SyncTarget | str,
        contact_id: int | None = None,
        force: bool = False,
        skip_if_already_mirrored: bool = True,
    ) -> ContactRoleSyncJob | None:
        role_n = CanonicalRole.parse(role)
        target_v = target.value if isinstance(target, SyncTarget) else str(target)
        if role_n is CanonicalRole.UNKNOWN and target_v == SyncTarget.WHATSAPP_UI.value:
            return None

        phone_n = normalize_phone_e164(phone) or (phone or "")
        existing = self.find_open(
            contact_key=contact_key, role=role_n.value, target=target_v
        )
        if existing and not force:
            return existing

        key = (contact_key, role_n.value, target_v)
        if skip_if_already_mirrored and not force and key in self._done_keys:
            return None

        if skip_if_already_mirrored and not force:
            for job in self.list_jobs():
                if (
                    job.contact_key == contact_key
                    and job.role == role_n.value
                    and job.target == target_v
                    and job.status == JobStatus.DONE.value
                ):
                    self._done_keys.add(key)
                    return None

        job = ContactRoleSyncJob(
            job_id=str(uuid.uuid4()),
            contact_key=contact_key,
            phone=phone_n,
            contact_id=contact_id,
            role=role_n.value,
            role_source=role_source,
            target=target_v,
            created_at=_now(),
            updated_at=_now(),
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
    ) -> ContactRoleSyncJob | None:
        status_v = status.value if isinstance(status, JobStatus) else str(status)
        with self._lock:
            jobs = self._read()
            found: ContactRoleSyncJob | None = None
            for i, raw in enumerate(jobs):
                if str(raw.get("job_id")) != job_id:
                    continue
                job = ContactRoleSyncJob.from_dict(raw)
                if bump_attempt:
                    job.attempt_count += 1
                job.status = status_v
                job.last_error = last_error
                if result_code:
                    job.result_code = result_code
                job.updated_at = _now()
                jobs[i] = job.to_dict()
                found = job
                if status_v == JobStatus.DONE.value:
                    self._done_keys.add((job.contact_key, job.role, job.target))
                break
            if found is not None:
                self._write(jobs)
            return found
