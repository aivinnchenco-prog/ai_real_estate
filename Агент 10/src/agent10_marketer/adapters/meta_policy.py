"""Deterministic Meta write / ACTIVE / budget safety guards."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Iterable

from agent10_marketer.adapters.meta_errors import (
    MetaActiveDisabled,
    MetaBudgetPolicyError,
    MetaSafetyError,
    MetaWriteDisabled,
)
from agent10_marketer.config import BudgetConfig, MetaConfig

# Status values Agent 10 may create at this stage.
FORCED_CREATE_STATUS = "PAUSED"
ACTIVE_STATUS = "ACTIVE"

# Objectives allowed for Meta create (Outcome-driven API naming).
DEFAULT_ALLOWED_OBJECTIVES: frozenset[str] = frozenset(
    {
        "OUTCOME_ENGAGEMENT",
        "OUTCOME_TRAFFIC",
        "OUTCOME_LEADS",
        "OUTCOME_AWARENESS",
        "OUTCOME_SALES",
        "OUTCOME_APP_PROMOTION",
    }
)


def assert_write_enabled(meta: MetaConfig) -> None:
    if not meta.write_enabled:
        raise MetaWriteDisabled(
            "META_WRITE_ENABLED=false — POST/PATCH/DELETE blocked before network"
        )


def assert_active_allowed(meta: MetaConfig, status: str | None) -> None:
    if status is None:
        return
    if str(status).upper() != ACTIVE_STATUS:
        return
    if not meta.active_enabled:
        raise MetaActiveDisabled(
            "ACTIVE launch disabled in current safety mode "
            "(META_ACTIVE_ENABLED=false)"
        )


def force_paused_status(requested: str | None, *, meta: MetaConfig) -> str:
    """Force PAUSED; reject ACTIVE unless META_ACTIVE_ENABLED (still not for auto-launch)."""
    requested_u = (requested or FORCED_CREATE_STATUS).upper()
    if requested_u == ACTIVE_STATUS:
        assert_active_allowed(meta, ACTIVE_STATUS)
        # Even if somehow enabled, V1 create path still forces PAUSED.
        raise MetaActiveDisabled(
            "ACTIVE launch disabled in current safety mode — create path forces PAUSED"
        )
    if requested_u not in {FORCED_CREATE_STATUS, "ARCHIVED"}:
        # Only PAUSED creates are in scope for V1 write path.
        if requested_u != FORCED_CREATE_STATUS:
            raise MetaSafetyError(
                f"create status must be PAUSED (got {requested_u})"
            )
    return FORCED_CREATE_STATUS


def assert_objective_allowed(
    objective: str,
    *,
    allowed: Iterable[str] | None = None,
) -> str:
    allowed_set = frozenset(allowed or DEFAULT_ALLOWED_OBJECTIVES)
    obj = str(objective or "").strip().upper()
    if obj not in allowed_set:
        raise MetaSafetyError(
            f"objective {objective!r} not in allowed set: {sorted(allowed_set)}"
        )
    return obj


def assert_budget_within_caps(
    budget: BudgetConfig,
    *,
    daily_budget: float | Decimal,
    duration_days: int,
    total_budget: float | Decimal | None = None,
) -> None:
    """Hard reject (not soft-cap) before any Meta write network call."""
    daily = Decimal(str(daily_budget))
    duration = int(duration_days)
    max_daily = Decimal(str(budget.max_daily_budget))
    max_duration = int(budget.max_duration_days)
    max_total = Decimal(str(budget.max_total_budget))

    if daily > max_daily:
        raise MetaBudgetPolicyError(
            f"daily_budget {daily} exceeds TARGET_MAX_DAILY_BUDGET {max_daily}"
        )
    if duration > max_duration:
        raise MetaBudgetPolicyError(
            f"duration_days {duration} exceeds TARGET_MAX_DURATION_DAYS {max_duration}"
        )
    computed_total = daily * Decimal(duration)
    total = Decimal(str(total_budget)) if total_budget is not None else computed_total
    if total > max_total or computed_total > max_total:
        raise MetaBudgetPolicyError(
            f"total_budget {max(total, computed_total)} exceeds "
            f"TARGET_MAX_TOTAL_BUDGET {max_total}"
        )


def thb_to_meta_minor_units(amount: float | Decimal) -> str:
    """Meta budgets are integer minor units (2 decimal currencies → *100)."""
    value = (Decimal(str(amount)) * Decimal(100)).quantize(Decimal("1"))
    if value < 0:
        raise MetaBudgetPolicyError("budget cannot be negative")
    return str(int(value))


_OBJECT_STORY_ID_RE = re.compile(r"^\d+_\d+$")


def validate_object_story_id(raw: object) -> str:
    """Accept only Meta-style page_post ids. Reject permalinks / empty / guesses."""
    if raw is None:
        raise MetaSafetyError("object_story_id required")
    text = str(raw).strip()
    if not text:
        raise MetaSafetyError("object_story_id required")
    lower = text.lower()
    if lower.startswith("http://") or lower.startswith("https://") or "/" in text:
        raise MetaSafetyError(
            "malformed object_story_id — permalink/URL is not valid; "
            "use Meta post id confirmed via GET (typically PAGEID_POSTID)"
        )
    if not _OBJECT_STORY_ID_RE.match(text):
        raise MetaSafetyError(
            f"malformed object_story_id {text!r} — expected digits_digits from Meta GET"
        )
    return text
