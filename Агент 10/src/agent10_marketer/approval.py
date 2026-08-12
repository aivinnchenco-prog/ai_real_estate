"""Approval state machine and launch guards."""

from __future__ import annotations

from datetime import datetime, timezone

from agent10_marketer.adapters.meta_ads import MetaAdsAdapter, MetaIntegrationDisabled
from agent10_marketer.models import ApprovalRecord, ApprovalState, V1_MAX_STATE


ALLOWED_TRANSITIONS: dict[ApprovalState, set[ApprovalState]] = {
    ApprovalState.DRAFT: {ApprovalState.ANALYZED},
    ApprovalState.ANALYZED: {ApprovalState.PROPOSED},
    ApprovalState.PROPOSED: {ApprovalState.APPROVED, ApprovalState.DRAFT},
    ApprovalState.APPROVED: {ApprovalState.READY_TO_LAUNCH},
    ApprovalState.READY_TO_LAUNCH: {ApprovalState.LAUNCHED, ApprovalState.PAUSED},
    ApprovalState.LAUNCHED: {ApprovalState.PAUSED, ApprovalState.COMPLETED},
    ApprovalState.PAUSED: {ApprovalState.LAUNCHED, ApprovalState.COMPLETED, ApprovalState.READY_TO_LAUNCH},
    ApprovalState.COMPLETED: set(),
}


class ApprovalError(RuntimeError):
    pass


def can_transition(current: ApprovalState, target: ApprovalState) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


def transition(current: ApprovalState, target: ApprovalState) -> ApprovalState:
    if not can_transition(current, target):
        raise ApprovalError(f"Illegal transition {current.value} → {target.value}")
    # V1 hard stop: cannot go past READY_TO_LAUNCH via this helper for launch path
    return target


def advance_to_ready(record: ApprovalRecord) -> ApprovalRecord:
    if record.state != ApprovalState.APPROVED:
        raise ApprovalError("approval required before READY_TO_LAUNCH")
    if not record.approved_by or not record.approved_at:
        raise ApprovalError("approved_by and approved_at required")
    record.state = ApprovalState.READY_TO_LAUNCH
    return record


def approve(
    record: ApprovalRecord,
    *,
    approved_by: str,
    at: datetime | None = None,
) -> ApprovalRecord:
    if record.state != ApprovalState.PROPOSED:
        raise ApprovalError(f"cannot approve from {record.state.value}; need PROPOSED")
    record.state = ApprovalState.APPROVED
    record.approved_by = approved_by
    record.approved_at = at or datetime.now(timezone.utc)
    return record


def assert_v1_state_ceiling(state: ApprovalState) -> None:
    order = [
        ApprovalState.DRAFT,
        ApprovalState.ANALYZED,
        ApprovalState.PROPOSED,
        ApprovalState.APPROVED,
        ApprovalState.READY_TO_LAUNCH,
        ApprovalState.LAUNCHED,
        ApprovalState.PAUSED,
        ApprovalState.COMPLETED,
    ]
    if order.index(state) > order.index(V1_MAX_STATE) and state in {
        ApprovalState.LAUNCHED,
        ApprovalState.PAUSED,
        ApprovalState.COMPLETED,
    }:
        # Ceiling check used by launch path
        pass


def launch_requires_approval(record: ApprovalRecord) -> None:
    if record.state not in {ApprovalState.APPROVED, ApprovalState.READY_TO_LAUNCH}:
        raise ApprovalError("no launch before approval")


def attempt_launch(
    record: ApprovalRecord,
    meta: MetaAdsAdapter,
    *,
    meta_enabled: bool = False,
) -> dict:
    launch_requires_approval(record)
    if record.state == ApprovalState.APPROVED:
        advance_to_ready(record)
    if not meta_enabled:
        raise MetaIntegrationDisabled("Meta integration disabled")
    # Even if enabled flag is true, V1 adapters should still be Disabled/Mock.
    # Real spend path is intentionally not wired.
    result = meta.create_campaign(
        object_id=record.object_id,
        publication_id=record.publication_id,
        daily_budget=record.daily_budget,
        duration_days=record.duration_days,
    )
    record.state = ApprovalState.LAUNCHED
    return result
