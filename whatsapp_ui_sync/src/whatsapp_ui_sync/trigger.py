"""Async enqueue hook for WhatsApp list sync (never blocks qualification)."""

from __future__ import annotations

from typing import Any

from .config import WhatsAppUiSyncConfig
from .phone import normalize_phone_e164
from .queue import RoleSyncJob, RoleSyncQueue
from .roles import CanonicalRole, role_changed


def enqueue_role_sync_if_changed(
    phone: str | None,
    new_role: CanonicalRole | str | None,
    *,
    previous_role: CanonicalRole | str | None = None,
    config: WhatsAppUiSyncConfig | None = None,
    queue: RoleSyncQueue | None = None,
) -> RoleSyncJob | None:
    """Enqueue outbox job when role first becomes known or changes.

    Fail-safe: any error returns None — callers must ignore failures.
    Does not open a browser.
    """
    try:
        cfg = config or WhatsAppUiSyncConfig.from_env()
        phone_n = normalize_phone_e164(phone)
        if not phone_n:
            return None
        nxt = CanonicalRole.parse(new_role)
        if nxt is CanonicalRole.UNKNOWN:
            return None
        if previous_role is not None and not role_changed(previous_role, nxt):
            return None
        q = queue or RoleSyncQueue(
            cfg.queue_dir / "role_sync_jobs.json",
            max_attempts=cfg.max_attempts,
        )
        return q.enqueue(
            phone_n,
            nxt,
            previous_role=previous_role,
            config=cfg,
            force=False,
        )
    except Exception:
        return None


def process_queue_once(
    *,
    config: WhatsAppUiSyncConfig | None = None,
    worker: Any = None,
    queue: RoleSyncQueue | None = None,
    confirm_write: bool = False,
) -> list[dict[str, Any]]:
    """Process PENDING jobs with bounded retries. Optional — not on message path."""
    from .results import NON_RETRYABLE_CODES, SyncCode
    from .worker import WhatsAppNativeListSyncWorker

    cfg = config or WhatsAppUiSyncConfig.from_env()
    q = queue or RoleSyncQueue(
        cfg.queue_dir / "role_sync_jobs.json",
        max_attempts=cfg.max_attempts,
    )
    w = worker or WhatsAppNativeListSyncWorker(cfg)
    results: list[dict[str, Any]] = []
    try:
        for job in q.pending():
            q.mark(job.job_id, status="RUNNING", bump_attempt=True)
            sync = w.sync_contact_role(
                job.phone,
                job.canonical_role,
                confirm_write=confirm_write,
            )
            payload = {"job_id": job.job_id, **sync.to_dict()}
            results.append(payload)
            if sync.code in {SyncCode.ALREADY_SYNCED, SyncCode.SYNCED, SyncCode.DRY_RUN_PLAN, SyncCode.SKIPPED_UNKNOWN, SyncCode.SKIPPED_DISABLED}:
                q.mark(
                    job.job_id,
                    status="DONE",
                    result_code=sync.code.value,
                    last_error="",
                )
            elif sync.code in NON_RETRYABLE_CODES or not sync.retryable:
                q.mark(
                    job.job_id,
                    status="FAILED",
                    result_code=sync.code.value,
                    last_error=sync.message,
                )
            else:
                # retryable — back to PENDING if attempts remain
                refreshed = q.list_jobs()
                current = next((j for j in refreshed if j.job_id == job.job_id), None)
                if current and current.attempt_count >= cfg.max_attempts:
                    q.mark(
                        job.job_id,
                        status="FAILED",
                        result_code=sync.code.value,
                        last_error=sync.message,
                    )
                else:
                    q.mark(
                        job.job_id,
                        status="PENDING",
                        result_code=sync.code.value,
                        last_error=sync.message,
                    )
    finally:
        if worker is None:
            try:
                w.close()
            except Exception:
                pass
    return results
