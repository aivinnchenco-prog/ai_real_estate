"""Frozen column order for «Аренда недвижимости — Доступность».

Matches the Notion table layout: month columns first (Sep 2026–Aug 2027),
technical fields at the end. The service never reorders Notion schema — it only
uses this list for display, readback, and write payload field ordering.
"""
from __future__ import annotations

LOCKED_TARGET_COLUMN_ORDER: tuple[str, ...] = (
    "Sep 26",
    "Oct 26",
    "Nov 26",
    "Dec 26",
    "Jan 27",
    "Feb 27",
    "Mar 27",
    "Apr 27",
    "May 27",
    "Jun 27",
    "Jul 27",
    "Aug 27",
    "Object ID",
    "Объект",
    "Source",
    "URL объекта календаря",
    "Last Checked",
    "Next Check",
    "Refresh Tier",
    "Refresh Status",
    "Source Status",
    "Last Error",
)

LOCKED_MONTH_COLUMNS: tuple[str, ...] = LOCKED_TARGET_COLUMN_ORDER[:12]
LOCKED_TECHNICAL_COLUMNS: tuple[str, ...] = LOCKED_TARGET_COLUMN_ORDER[12:]


def locked_columns_present(property_names: set[str] | list[str]) -> list[str]:
    """Return locked order filtered to columns that exist in the live schema."""
    names = set(property_names)
    return [name for name in LOCKED_TARGET_COLUMN_ORDER if name in names]


def validate_locked_target_schema(properties: dict[str, dict]) -> tuple[bool, str]:
    """Ensure live Notion schema matches the frozen column set (order not checked via API)."""
    names = set(properties.keys())
    locked = set(LOCKED_TARGET_COLUMN_ORDER)
    missing = [c for c in LOCKED_TARGET_COLUMN_ORDER if c not in names]
    extra = sorted(names - locked)
    if missing:
        return False, f"FAIL: missing locked columns {missing}"
    if extra:
        return False, f"FAIL: unexpected columns {extra}"
    return True, "PASS"
