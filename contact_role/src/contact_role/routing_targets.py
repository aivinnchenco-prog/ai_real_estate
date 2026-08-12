"""Route targets derived from canonical role (no assign imports)."""

from __future__ import annotations

from enum import Enum

from .roles import CanonicalRole


class RouteTarget(str, Enum):
    AGENT_6 = "AGENT_6"
    AGENT_7 = "AGENT_7"
    CLASSIFIER = "CLASSIFIER"


def route_for_role(role: CanonicalRole | str | None) -> RouteTarget:
    r = CanonicalRole.parse(role)
    if r is CanonicalRole.CLIENT:
        return RouteTarget.AGENT_6
    if r in {CanonicalRole.OWNER, CanonicalRole.AGENT}:
        return RouteTarget.AGENT_7
    return RouteTarget.CLASSIFIER
