"""Age normalization helpers (no simulated time-series history)."""

from __future__ import annotations

from agent10_marketer.config import ScoringConfig


def normalize_by_age(
    metric: float | None,
    age_hours: float | None,
    *,
    min_age_hours: float,
) -> float | None:
    """metric / max(age_hours, minimum_age). Returns None if inputs missing."""
    if metric is None or age_hours is None:
        return None
    denom = max(float(age_hours), max(float(min_age_hours), 1e-9))
    return float(metric) / denom


def window_hours(name: str, config: ScoringConfig) -> int | None:
    return config.windows_hours.get(name)


def within_window(age_hours: float | None, window_name: str, config: ScoringConfig) -> bool:
    """If age unknown, do not fake membership — treat as outside strict window filters."""
    hours = window_hours(window_name, config)
    if hours is None or age_hours is None:
        return False
    return float(age_hours) <= float(hours)
