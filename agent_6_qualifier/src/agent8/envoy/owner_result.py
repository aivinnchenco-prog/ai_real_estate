"""Compatibility shim. Canonical import: ``agent7_envoy.owner_result``."""
from agent7_envoy.owner_result import (
    OwnerVerdict,
    apply_verdict_to_pending,
    apply_verdict_to_session,
    build_client_message,
    build_client_message_for_object,
    notion_availability_update,
    parse_owner_reply,
)

__all__ = [
    "OwnerVerdict",
    "apply_verdict_to_pending",
    "apply_verdict_to_session",
    "build_client_message",
    "build_client_message_for_object",
    "notion_availability_update",
    "parse_owner_reply",
]
