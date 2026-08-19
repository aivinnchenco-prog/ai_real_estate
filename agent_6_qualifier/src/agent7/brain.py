"""Compatibility shim. Canonical import: ``agent6_qualifier.brain``."""
from agent6_qualifier.brain import (
    EXTRACT_SCHEMA,
    _call,
    _text,
    apply_update,
    extract_lead_update,
    polish_reply,
)

__all__ = [
    "EXTRACT_SCHEMA",
    "_call",
    "_text",
    "apply_update",
    "extract_lead_update",
    "polish_reply",
]
