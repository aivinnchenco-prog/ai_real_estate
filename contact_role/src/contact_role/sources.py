"""How a canonical role became known."""

from __future__ import annotations

from enum import Enum


class RoleSource(str, Enum):
    MANUAL = "MANUAL"
    AGENT7_OUTREACH = "AGENT7_OUTREACH"
    AGENT6_INBOUND = "AGENT6_INBOUND"
    INBOUND_LEAD = "INBOUND_LEAD"
    WORKFLOW_CONTEXT = "WORKFLOW_CONTEXT"
    MESSAGE_CLASSIFICATION = "MESSAGE_CLASSIFICATION"
    EXISTING_CRM = "EXISTING_CRM"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, value: str | RoleSource | None) -> RoleSource:
        if value is None:
            return cls.UNKNOWN
        if isinstance(value, RoleSource):
            return value
        text = str(value).strip().upper()
        if not text:
            return cls.UNKNOWN
        try:
            return cls(text)
        except ValueError:
            return cls.UNKNOWN


# Explicit workflow sources (beat message classification).
EXPLICIT_WORKFLOW_SOURCES = frozenset(
    {
        RoleSource.AGENT7_OUTREACH,
        RoleSource.AGENT6_INBOUND,
        RoleSource.INBOUND_LEAD,
        RoleSource.WORKFLOW_CONTEXT,
    }
)
