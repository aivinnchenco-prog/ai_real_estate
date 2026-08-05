"""Compatibility shim. Canonical import: ``agent8.envoy.owner_result``."""
from agent8.envoy.owner_result import (
    OwnerVerdict,
    apply_verdict_to_session,
    build_client_message,
    notion_availability_update,
    parse_owner_reply,
)

__all__ = [
    "OwnerVerdict",
    "apply_verdict_to_session",
    "build_client_message",
    "notion_availability_update",
    "parse_owner_reply",
]
