"""Budget recommendation with hard caps."""

from __future__ import annotations

from agent10_marketer.config import BudgetConfig
from agent10_marketer.models import CampaignSuggestion


def suggest_campaign(
    budget: BudgetConfig,
    *,
    daily_budget: float | None = None,
    duration_days: int | None = None,
    objective: str | None = None,
) -> CampaignSuggestion:
    notes: list[str] = []
    capped = False

    daily = float(budget.default_daily_budget if daily_budget is None else daily_budget)
    if daily > budget.max_daily_budget:
        notes.append(
            f"daily_budget capped from {daily} to {budget.max_daily_budget}"
        )
        daily = float(budget.max_daily_budget)
        capped = True
    if daily < 0:
        daily = 0.0
        capped = True
        notes.append("daily_budget floored at 0")

    duration = int(budget.default_duration_days if duration_days is None else duration_days)
    if duration > budget.max_duration_days:
        notes.append(
            f"duration_days capped from {duration} to {budget.max_duration_days}"
        )
        duration = int(budget.max_duration_days)
        capped = True
    if duration < 1:
        duration = 1
        capped = True
        notes.append("duration_days floored at 1")

    raw_total = daily * duration
    max_total = float(budget.max_total_budget)
    if raw_total > max_total:
        # Prefer keeping duration; shrink daily if needed to respect total cap.
        if duration > 0:
            daily = min(daily, max_total / duration)
        notes.append(f"total spend capped at {max_total}")
        capped = True
        raw_total = daily * duration

    # Final clamp: never exceed max_total
    max_total_out = min(raw_total, max_total)

    return CampaignSuggestion(
        objective=objective or budget.default_objective,
        daily_budget=round(daily, 2),
        duration_days=duration,
        max_total_budget=round(max_total_out, 2),
        strategy=budget.default_strategy,
        placements_mode=budget.default_placements_mode,
        audience_mode=budget.default_audience_mode,
        currency=budget.currency,
        capped=capped,
        cap_notes=notes,
    )
