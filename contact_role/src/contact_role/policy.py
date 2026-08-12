"""Deterministic role-source priority and overwrite rules."""

from __future__ import annotations

from .roles import CanonicalRole
from .sources import EXPLICIT_WORKFLOW_SOURCES, RoleSource
from .state import ContactRoleState

# Higher number wins. Preferred order from the brief:
# 1 MANUAL
# 2 EXPLICIT WORKFLOW CONTEXT
# 3 EXISTING CONFIRMED CRM ROLE
# 4 MESSAGE CLASSIFICATION
# 5 UNKNOWN
ROLE_SOURCE_PRIORITY: dict[RoleSource, int] = {
    RoleSource.MANUAL: 100,
    RoleSource.AGENT7_OUTREACH: 80,
    RoleSource.AGENT6_INBOUND: 80,
    RoleSource.INBOUND_LEAD: 80,
    RoleSource.WORKFLOW_CONTEXT: 80,
    RoleSource.EXISTING_CRM: 60,
    RoleSource.MESSAGE_CLASSIFICATION: 40,
    RoleSource.UNKNOWN: 0,
}


def source_rank(source: RoleSource | str | None) -> int:
    return ROLE_SOURCE_PRIORITY.get(RoleSource.parse(source), 0)


def can_overwrite_role(
    current: ContactRoleState | None,
    *,
    new_role: CanonicalRole,
    new_source: RoleSource,
    allow_reevaluation: bool = False,
) -> bool:
    """Return True if new_source may replace the stored role."""
    if current is None:
        return new_role is not CanonicalRole.UNKNOWN or new_source is RoleSource.MANUAL

    if current.locked_by_manual_override and new_source is not RoleSource.MANUAL:
        return False

    cur_role = CanonicalRole.parse(current.canonical_role)
    nxt = CanonicalRole.parse(new_role)
    cur_src = RoleSource.parse(current.role_source)

    if nxt is CanonicalRole.UNKNOWN and new_source is not RoleSource.MANUAL:
        return False

    if cur_role is CanonicalRole.UNKNOWN:
        return True

    if cur_role == nxt:
        # Same role: allow source upgrade / refresh metadata, but not demotion.
        return source_rank(new_source) >= source_rank(cur_src)

    # Different role: new source must outrank old, unless explicit re-eval unlock.
    if allow_reevaluation and new_source in EXPLICIT_WORKFLOW_SOURCES | {
        RoleSource.MANUAL
    }:
        return True

    if source_rank(new_source) > source_rank(cur_src):
        return True

    # Equal rank only for MANUAL replacing MANUAL, or same explicit workflow
    # with an explicit re-evaluation flag.
    if source_rank(new_source) == source_rank(cur_src):
        if new_source is RoleSource.MANUAL:
            return True
        return allow_reevaluation

    return False
