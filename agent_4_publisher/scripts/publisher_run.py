#!/usr/bin/env python3
"""Publisher batch orchestration: process lock, slot isolation, aggregated results."""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from publish_pipeline import (
    ALL_PUBLISH_PLATFORMS,
    default_schedule_time,
    get_prop,
    is_agent6_locked,
    notion_checkbox_property,
    notion_date_property,
    notion_get_page,
    notion_update_fields,
    phone_publisher_enabled,
    platform_jobs,
    publish_one,
    utc_today_iso,
)


@dataclass
class PublishSlot:
    platform: str
    mode: str | None
    content_type: str
    scheduled_time: str
    external: bool = False

    @property
    def slot_key(self) -> str:
        return f"{self.platform}:{self.content_type}"


@dataclass
class PublishRunContext:
    page_id: str
    run_id: str
    dry_run: bool
    force: bool
    retry_failed: bool
    owns_lock: bool = False
    bypass_lock: bool = False


@dataclass
class BatchResult:
    status: str = ""
    published: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    pending_external: list[str] = field(default_factory=list)
    lock_released: bool = False
    blocked_reason: str | None = None
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "published": list(self.published),
            "failed": list(self.failed),
            "skipped": list(self.skipped),
            "pending_external": list(self.pending_external),
            "lock_released": self.lock_released,
            "run_id": self.run_id,
            **({"blocked_reason": self.blocked_reason} if self.blocked_reason else {}),
        }


def slot_content_type(platform: str, mode: str | None, upload_video: bool | None = None) -> str:
    if mode == "carousel":
        return "carousel"
    if mode == "video":
        return "video"
    if upload_video:
        return "video"
    return "post"


def phone_external_slots(config: dict[str, Any]) -> list[PublishSlot]:
    if not phone_publisher_enabled(config):
        return []
    pp = config.get("phone_publisher") or {}
    mode = str(pp.get("mode") or "").strip().lower()
    if mode not in ("fb_only", "facebook_only"):
        return []
    channels = list(pp.get("channels") or ["fb_groups", "fb_marketplace"])
    scheduled = default_schedule_time()
    slots: list[PublishSlot] = []
    for channel in channels:
        if channel == "fb_groups":
            slots.append(
                PublishSlot(
                    platform="facebook",
                    mode=None,
                    content_type="groups",
                    scheduled_time=scheduled,
                    external=True,
                )
            )
        elif channel == "fb_marketplace":
            slots.append(
                PublishSlot(
                    platform="facebook",
                    mode=None,
                    content_type="marketplace",
                    scheduled_time=scheduled,
                    external=True,
                )
            )
    return slots


def enumerate_api_slots(
    platforms: list[str],
    config: dict[str, Any],
    *,
    post_mode: str | None,
    scheduled_time: str,
) -> list[PublishSlot]:
    slots: list[PublishSlot] = []
    for platform in platforms:
        for job_mode, job_time in platform_jobs(platform, scheduled_time, config, post_mode):
            ct = slot_content_type(platform, job_mode)
            slots.append(
                PublishSlot(
                    platform=platform,
                    mode=job_mode,
                    content_type=ct,
                    scheduled_time=job_time,
                    external=False,
                )
            )
    return slots


def try_acquire_process_lock(
    page_id: str,
    fields: dict[str, str],
    config: dict[str, Any],
    ctx: PublishRunContext,
) -> str | None:
    """Acquire agent6_locked before network I/O. Returns block reason or None."""
    if ctx.dry_run:
        ctx.owns_lock = False
        ctx.bypass_lock = True
        return None

    page = notion_get_page(page_id)
    if is_agent6_locked(page, fields) and not ctx.force:
        return "agent6_locked"

    lock_field = fields.get("agent6_locked")
    if not lock_field:
        ctx.owns_lock = True
        ctx.bypass_lock = True
        return None

    taken_props: dict[str, Any] = {lock_field: notion_checkbox_property(True)}
    taken_at_field = fields.get("agent6_taken_at")
    if taken_at_field and not get_prop(page, taken_at_field, "date"):
        taken_props[taken_at_field] = notion_date_property(utc_today_iso())

    notion_update_fields(page_id, taken_props)
    ctx.owns_lock = True
    ctx.bypass_lock = True
    return None


def should_keep_process_lock(result: BatchResult) -> bool:
    """Keep agent6_locked after a successful/idempotent run.

    Status stays ready_to_post by design; the lock is what stops chain-watcher
    from scheduling the same object again. Release only when nothing was
    published and the run failed (so a clean retry can acquire the lock).
    """
    if result.published:
        return True
    if result.status in {"awaiting_phone", "nothing_to_publish", "success"}:
        return True
    if result.skipped and not result.failed:
        return True
    return False


def release_process_lock(
    page_id: str,
    fields: dict[str, str],
    ctx: PublishRunContext,
) -> bool:
    if not ctx.owns_lock or ctx.dry_run:
        return False
    lock_field = fields.get("agent6_locked")
    if not lock_field:
        return False
    try:
        notion_update_fields(page_id, {lock_field: notion_checkbox_property(False)})
        ctx.owns_lock = False
        return True
    except Exception as exc:
        print(
            f"[publisher] lock release failed page_id={page_id} run_id={ctx.run_id}: {exc}",
            file=sys.stderr,
        )
        return False


def _classify_slot_outcome(out: dict[str, Any], slot: PublishSlot, result: BatchResult) -> None:
    key = slot.slot_key
    if out.get("skipped"):
        reason = str(out.get("reason") or "")
        if "already published" in reason:
            result.skipped.append(key)
        else:
            result.skipped.append(key)
        return
    if out.get("dry_run"):
        result.skipped.append(f"{key}:dry-run")
        return
    if out.get("error") or out.get("failed"):
        result.failed.append(key)
        return
    result.published.append(key)


def finalize_batch_status(result: BatchResult) -> str:
    has_api = bool(result.published or result.failed or result.skipped)
    if result.pending_external and not result.published and not result.failed:
        if result.skipped and not any(s for s in result.skipped if ":dry-run" not in s):
            return "awaiting_phone"
        if not has_api:
            return "awaiting_phone"
    if result.published and not result.failed:
        if result.pending_external:
            return "awaiting_phone"
        return "success"
    if result.published and result.failed:
        return "partial_success"
    if result.failed and not result.published:
        return "failed"
    if result.skipped and not result.published and not result.failed:
        return "nothing_to_publish"
    if result.pending_external:
        return "awaiting_phone"
    return "failed"


def publish_page_batch(
    page_id: str,
    platforms: list[str],
    config: dict[str, Any],
    *,
    dry_run: bool = False,
    force: bool = False,
    retry_failed: bool = False,
    post_mode: str | None = None,
    scheduled_time: str | None = None,
) -> BatchResult:
    fields = config["notion"]["fields"]
    run_id = uuid.uuid4().hex[:12]
    ctx = PublishRunContext(
        page_id=page_id,
        run_id=run_id,
        dry_run=dry_run,
        force=force,
        retry_failed=retry_failed,
    )
    result = BatchResult(run_id=run_id)
    scheduled = scheduled_time or default_schedule_time()

    api_slots = enumerate_api_slots(platforms, config, post_mode=post_mode, scheduled_time=scheduled)
    external_slots = phone_external_slots(config)
    result.pending_external = [s.slot_key for s in external_slots]

    block_reason = try_acquire_process_lock(page_id, fields, config, ctx)
    if block_reason:
        result.status = "blocked"
        result.blocked_reason = block_reason
        print(
            json_batch_log(
                result,
                extra={"page_id": page_id, "message": f"blocked: {block_reason}"},
            )
        )
        return result

    try:
        for slot in api_slots:
            try:
                out = publish_one(
                    page_id,
                    slot.platform,
                    slot.scheduled_time,
                    dry_run,
                    config,
                    force=force,
                    bypass_lock=ctx.bypass_lock,
                    mode=slot.mode,
                    run_id=run_id,
                )
            except Exception as exc:
                print(
                    f"ERROR {slot.platform}/{slot.mode or 'auto'}: {exc}",
                    file=sys.stderr,
                )
                result.failed.append(slot.slot_key)
                continue
            _classify_slot_outcome(out, slot, result)
            print(json_slot_output(out))

        result.status = finalize_batch_status(result)
    finally:
        if should_keep_process_lock(result):
            # Intentional: leave agent6_locked=true so chain does not re-queue.
            result.lock_released = False
            ctx.owns_lock = False
        else:
            result.lock_released = release_process_lock(page_id, fields, ctx)

    print(json_batch_log(result, extra={"page_id": page_id}))
    return result


def json_slot_output(out: dict[str, Any]) -> str:
    import json

    return json.dumps(out, indent=2, ensure_ascii=False)


def json_batch_log(result: BatchResult, *, extra: dict[str, Any] | None = None) -> str:
    import json

    payload = result.to_dict()
    if extra:
        payload.update(extra)
    return json.dumps(payload, indent=2, ensure_ascii=False)


def default_platforms_for_all() -> list[str]:
    return list(ALL_PUBLISH_PLATFORMS)
