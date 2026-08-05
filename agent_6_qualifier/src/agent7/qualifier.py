"""Compatibility shim. Canonical import: ``agent6_qualifier.qualifier``."""
from agent6_qualifier.qualifier import (
    Qualifier,
    Session,
    Turn,
    is_consent,
    is_decline,
    prefers_chosen_only,
    skips_alternatives,
)

__all__ = [
    "Qualifier",
    "Session",
    "Turn",
    "is_consent",
    "is_decline",
    "prefers_chosen_only",
    "skips_alternatives",
]
