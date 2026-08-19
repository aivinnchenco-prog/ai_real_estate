"""ContactRoleSyncCoordinator — fan-out amoCRM + WhatsApp UI after role commit."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..flags import (
    amo_dry_run,
    amo_process_async,
    amo_sync_enabled,
    whatsapp_ui_sync_enabled,
)
from ..roles import CanonicalRole
from ..state import ContactRoleState, ContactRoleStore, default_store_path
from .amocrm import AmoContactRoleSync, AmoRoleSyncResult
from .outbox import ContactRoleSyncOutbox, JobStatus, SyncTarget
from .whatsapp import WhatsAppEnqueueResult, enqueue_whatsapp_ui_sync


def default_outbox_path() -> Path:
    override = (os.getenv("CONTACT_ROLE_OUTBOX_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    return default_store_path().parent / "contact_role_sync_jobs.json"


@dataclass
class SyncFanOutResult:
    amocrm_job_id: str | None = None
    whatsapp_job_id: str | None = None
    amocrm_code: str = ""
    whatsapp_code: str = ""
    amocrm_error: str = ""
    whatsapp_error: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "amocrm_job_id": self.amocrm_job_id,
            "whatsapp_job_id": self.whatsapp_job_id,
            "amocrm_code": self.amocrm_code,
            "whatsapp_code": self.whatsapp_code,
            "amocrm_error": self.amocrm_error,
            "whatsapp_error": self.whatsapp_error,
            "notes": list(self.notes),
        }


class ContactRoleSyncCoordinator:
    """After committed canonical role: enqueue independent mirror syncs.

    Failures are isolated: amo failure does not block WhatsApp and vice versa.
    Playwright is never run inline — uses existing queue/outbox.
    Conversation / Agent 7 send is never blocked by mirror processing.
    """

    def __init__(
        self,
        *,
        store: ContactRoleStore | None = None,
        outbox: ContactRoleSyncOutbox | None = None,
        amo_sync: AmoContactRoleSync | None = None,
        process_inline_dry_run: bool = False,
        process_amo_async: bool | None = None,
    ):
        self.store = store or ContactRoleStore()
        self.outbox = outbox or ContactRoleSyncOutbox(default_outbox_path())
        self.amo_sync = amo_sync or AmoContactRoleSync(
            dry_run=amo_dry_run(),
            enabled=amo_sync_enabled(),
        )
        self.process_inline_dry_run = process_inline_dry_run
        self.process_amo_async = (
            amo_process_async() if process_amo_async is None else bool(process_amo_async)
        )

    def fan_out(
        self,
        state: ContactRoleState,
        *,
        previous_role: CanonicalRole | str | None = None,
        force: bool = False,
    ) -> SyncFanOutResult:
        role = CanonicalRole.parse(state.canonical_role)
        result = SyncFanOutResult()
        contact_id = state.metadata.get("contact_id")
        if contact_id is not None:
            try:
                contact_id = int(contact_id)
            except (TypeError, ValueError):
                contact_id = None

        # --- amoCRM outbox ---
        try:
            # process_inline_dry_run=True is used by offline tests / diagnose plans.
            amo_on = bool(
                amo_sync_enabled()
                or (self.amo_sync and self.amo_sync.enabled)
                or self.process_inline_dry_run
            )
            if not amo_on:
                result.amocrm_code = "DISABLED"
                result.notes.append(
                    "amoCRM sync disabled — canonical/routing unchanged"
                )
            else:
                amo_job = self.outbox.enqueue(
                    contact_key=state.contact_key,
                    phone=state.phone,
                    role=role,
                    role_source=state.role_source,
                    target=SyncTarget.AMOCRM,
                    contact_id=contact_id,
                    force=force,
                )
                if amo_job is None:
                    result.amocrm_code = "ALREADY_SYNCED"
                    result.notes.append("amoCRM: no new job (idempotent)")
                else:
                    result.amocrm_job_id = amo_job.job_id
                    result.amocrm_code = "ENQUEUED"
                    if self.process_inline_dry_run:
                        processed = self._process_amo_job(amo_job.job_id)
                        result.amocrm_code = processed.code
                        if processed.code in {
                            "FAILED",
                            "AMO_UNAVAILABLE",
                            "FIELD_MISSING",
                        }:
                            result.amocrm_error = processed.message
                    elif self.process_amo_async:
                        threading.Thread(
                            target=self._process_amo_job,
                            args=(amo_job.job_id,),
                            daemon=True,
                            name="contact-role-amo-sync",
                        ).start()
                        result.amocrm_code = "ENQUEUED_ASYNC"
                        result.notes.append(
                            "amoCRM: processing async (non-blocking)"
                        )
        except Exception as exc:
            result.amocrm_code = "FAILED"
            result.amocrm_error = str(exc)[:200]
            result.notes.append("amoCRM enqueue/process isolated failure")

        # --- WhatsApp UI (existing Playwright outbox) ---
        try:
            wa_on = bool(whatsapp_ui_sync_enabled() or self.process_inline_dry_run)
            if not wa_on:
                result.whatsapp_code = "DISABLED"
                result.notes.append(
                    "WhatsApp UI sync disabled — canonical/routing/amo unchanged"
                )
            else:
                wa_job = self.outbox.enqueue(
                    contact_key=state.contact_key,
                    phone=state.phone,
                    role=role,
                    role_source=state.role_source,
                    target=SyncTarget.WHATSAPP_UI,
                    contact_id=contact_id,
                    force=force,
                )
                if wa_job is None and role is CanonicalRole.UNKNOWN:
                    result.whatsapp_code = "SKIPPED"
                    result.notes.append("WhatsApp: UNKNOWN no-op")
                elif wa_job is None:
                    result.whatsapp_code = "ALREADY_SYNCED"
                    result.notes.append("WhatsApp: no new coordinator job (idempotent)")
                else:
                    result.whatsapp_job_id = wa_job.job_id
                    wa = enqueue_whatsapp_ui_sync(
                        state.phone,
                        role,
                        previous_role=previous_role,
                    )
                    result.whatsapp_code = wa.code
                    if wa.code == "WHATSAPP_ENQUEUE_FAILED":
                        result.whatsapp_error = wa.message
                        self.outbox.mark(
                            wa_job.job_id,
                            status=JobStatus.FAILED,
                            result_code=wa.code,
                            last_error=wa.message,
                        )
                    elif wa.code == "SKIPPED":
                        self.outbox.mark(
                            wa_job.job_id,
                            status=JobStatus.SKIPPED,
                            result_code=wa.code,
                        )
                    else:
                        self.outbox.mark(
                            wa_job.job_id,
                            status=JobStatus.DONE,
                            result_code=wa.code,
                        )
        except Exception as exc:
            result.whatsapp_code = "FAILED"
            result.whatsapp_error = str(exc)[:200]
            result.notes.append("WhatsApp enqueue isolated failure")

        return result

    def _process_amo_job(self, job_id: str) -> AmoRoleSyncResult:
        jobs = {j.job_id: j for j in self.outbox.list_jobs()}
        job = jobs.get(job_id)
        if job is None:
            return AmoRoleSyncResult(code="FAILED", message="job missing")
        self.outbox.mark(job_id, status=JobStatus.RUNNING, bump_attempt=True)
        try:
            confirm = bool(self.amo_sync.enabled and not self.amo_sync.dry_run)
            sync = self.amo_sync.sync_role(
                phone=job.phone,
                role=job.role,
                contact_id=job.contact_id,
                confirm_write=confirm,
            )
            status = JobStatus.DONE
            if sync.code in {
                "FAILED",
                "AMO_UNAVAILABLE",
                "CONTACT_NOT_FOUND",
                "AMBIGUOUS_CONTACT",
                "FIELD_MISSING",
            }:
                status = JobStatus.FAILED
            # DRY_RUN_PLAN / SYNCED / ALREADY_SYNCED → DONE
            if sync.code in {"DRY_RUN_PLAN", "SYNCED", "ALREADY_SYNCED"}:
                status = JobStatus.DONE
            self.outbox.mark(
                job_id,
                status=status,
                result_code=sync.code,
                last_error=sync.message if status == JobStatus.FAILED else "",
            )
            return sync
        except Exception as exc:
            self.outbox.mark(
                job_id,
                status=JobStatus.FAILED,
                result_code="FAILED",
                last_error=str(exc)[:200],
            )
            return AmoRoleSyncResult(code="FAILED", message=str(exc)[:200])
