"""Single-object Facebook Marketplace status refresh + Notion upsert."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import AvailabilityConfig
from .models import RefreshStatus, RefreshTier, SourceKind, SourceStatus
from .notion_reader import NotionReader, map_target_fields
from .notion_writer import NotionWriter
from .repository import AvailabilityRepository
from .scheduler import AvailabilityScheduler
from .sync_one import _find_source_property
from ..providers.facebook import FacebookAvailabilityProvider
from ..providers.facebook_checker import FacebookCheckResult


@dataclass
class FacebookRefreshResult:
    object_id: str = ""
    name: str = ""
    success: bool = False
    business_status: str = ""
    technical_outcome: str = ""
    status_reason: str = ""
    notion_action: str = ""
    notion_page_id: str = ""
    elapsed_s: float = 0.0
    error: str = ""
    preserved_source_status: str = ""


def run_facebook_refresh(
    object_id: str,
    *,
    config: AvailabilityConfig,
    reader: NotionReader,
    writer: NotionWriter,
    repo: AvailabilityRepository,
    scheduler: AvailabilityScheduler,
    target_schema,
    target_mapping,
    confirm_write: bool = True,
) -> FacebookRefreshResult:
    out = FacebookRefreshResult(object_id=object_id)
    start = time.perf_counter()
    now = datetime.now(timezone.utc)

    state = repo.get_object(object_id)
    if state is None:
        out.error = "object not in sqlite"
        return out
    if state.source != SourceKind.FACEBOOK:
        out.error = f"source is {state.source.value}, expected FACEBOOK"
        return out

    preserved = state.source_status
    out.preserved_source_status = preserved.value

    prop = _find_source_property(
        reader, config, object_id, expected_source=SourceKind.FACEBOOK
    )
    out.name = prop.name

    provider = FacebookAvailabilityProvider()
    check: FacebookCheckResult = provider.check_listing_status(prop, config)

    if check.is_technical_error:
        out.technical_outcome = check.outcome
        out.status_reason = check.status_reason
        out.error = f"{check.outcome}: {check.status_reason}"
        nxt, _ = scheduler.schedule_retry(object_id, out.error, now=now)
        state = repo.get_object(object_id) or state
        state.status_reason = check.status_reason
        repo.save_object(state, now=now)
        if confirm_write and config.writes_allowed:
            notion_source_status = state.source_status
            if notion_source_status not in (SourceStatus.ACTIVE, SourceStatus.SOLD):
                notion_source_status = None
            action, page_id = writer.sync_batch_notion_upsert(
                object_id=object_id,
                object_name=prop.name,
                source=SourceKind.FACEBOOK,
                calendar_url=prop.source_url or "",
                batch_allowed_ids=frozenset({object_id}),
                month_cells={},
                last_checked=now,
                next_check=nxt,
                refresh_status=RefreshStatus.ERROR,
                last_error=out.error[:500],
                target_schema=target_schema,
                target_mapping=target_mapping,
                refresh_tier=state.refresh_tier,
                source_status=notion_source_status,
            )
            out.notion_action = action
            out.notion_page_id = page_id
        out.elapsed_s = round(time.perf_counter() - start, 2)
        return out

    business = check.business_status
    if business is None:
        out.technical_outcome = check.outcome
        out.error = "unclassified business status"
        scheduler.schedule_retry(object_id, out.error, now=now)
        out.elapsed_s = round(time.perf_counter() - start, 2)
        return out

    out.business_status = business
    out.status_reason = check.status_reason
    source_status = SourceStatus.ACTIVE if business == "ACTIVE" else SourceStatus.SOLD

    state.source_status = source_status
    state.status_reason = check.status_reason
    state.last_calendar_refresh_at = now
    repo.save_object(state, now=now)

    nxt = scheduler.schedule_success(object_id, now=now)
    out.success = True

    if confirm_write and config.writes_allowed:
        action, page_id = writer.sync_batch_notion_upsert(
            object_id=object_id,
            object_name=prop.name,
            source=SourceKind.FACEBOOK,
            calendar_url=prop.source_url or "",
            batch_allowed_ids=frozenset({object_id}),
            month_cells={},
            last_checked=now,
            next_check=nxt,
            refresh_status=RefreshStatus.SUCCESS,
            last_error="",
            target_schema=target_schema,
            target_mapping=target_mapping,
            refresh_tier=RefreshTier.H78,
            source_status=source_status,
        )
        out.notion_action = action
        out.notion_page_id = page_id

    out.elapsed_s = round(time.perf_counter() - start, 2)
    return out
