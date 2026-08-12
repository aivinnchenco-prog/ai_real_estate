"""Canonical contact roles → WhatsApp native list names.

Roles are owned by Agent 6 / Agent 7 / amoCRM. This module only maps.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class CanonicalRole(str, Enum):
    CLIENT = "CLIENT"
    OWNER = "OWNER"
    AGENT = "AGENT"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, value: str | CanonicalRole | None) -> CanonicalRole:
        if value is None:
            return cls.UNKNOWN
        if isinstance(value, CanonicalRole):
            return value
        text = str(value).strip().upper()
        if not text:
            return cls.UNKNOWN
        # Notion / legacy aliases → canonical
        aliases = {
            "CLIENT": cls.CLIENT,
            "КЛИЕНТ": cls.CLIENT,
            "OWNER": cls.OWNER,
            "ВЛАДЕЛЕЦ": cls.OWNER,
            "СОБСТВЕННИК": cls.OWNER,
            "AGENT": cls.AGENT,
            "АГЕНТ": cls.AGENT,
            "UNKNOWN": cls.UNKNOWN,
            "УТОЧНЯЕТСЯ": cls.UNKNOWN,
        }
        return aliases.get(text, cls.UNKNOWN)


@dataclass(frozen=True)
class ListPlan:
    """Planned native-list membership for a known role."""

    target_list: str | None
    must_assign: tuple[str, ...]
    must_remove: tuple[str, ...]
    no_op: bool = False


def map_role_to_list(
    role: CanonicalRole | str,
    *,
    client_list: str = "Client",
    owner_list: str = "Owner",
    agent_list: str = "Owner",
) -> str | None:
    """Return target list name, or None for UNKNOWN / no action."""
    canonical = CanonicalRole.parse(role)
    if canonical is CanonicalRole.CLIENT:
        return client_list
    if canonical is CanonicalRole.OWNER:
        return owner_list
    if canonical is CanonicalRole.AGENT:
        return agent_list
    return None


def plan_list_membership(
    role: CanonicalRole | str,
    *,
    current_lists: Iterable[str] = (),
    client_list: str = "Client",
    owner_list: str = "Owner",
    agent_list: str = "Owner",
) -> ListPlan:
    """Compute assign/remove for Client ↔ Owner conflict rules.

    CLIENT → Client assigned, Owner not assigned
    OWNER / AGENT → Owner (or agent_list) assigned, Client not assigned
    UNKNOWN → no action
    """
    canonical = CanonicalRole.parse(role)
    target = map_role_to_list(
        canonical,
        client_list=client_list,
        owner_list=owner_list,
        agent_list=agent_list,
    )
    if target is None:
        return ListPlan(target_list=None, must_assign=(), must_remove=(), no_op=True)

    managed = {client_list, owner_list, agent_list}
    current = {name for name in current_lists if name in managed}

    must_assign: list[str] = []
    must_remove: list[str] = []

    if target not in current:
        must_assign.append(target)

    for name in current:
        if name != target:
            must_remove.append(name)

    # Also remove the other managed list if it is the conflict pair even when
    # agent_list == owner_list (Client vs Owner only).
    conflict = client_list if target != client_list else owner_list
    if conflict != target and conflict in current and conflict not in must_remove:
        must_remove.append(conflict)

    already = not must_assign and not must_remove
    return ListPlan(
        target_list=target,
        must_assign=tuple(must_assign),
        must_remove=tuple(must_remove),
        no_op=already,
    )


def role_changed(
    previous: CanonicalRole | str | None,
    new: CanonicalRole | str | None,
) -> bool:
    """True when sync should be triggered (first known or role change)."""
    prev = CanonicalRole.parse(previous)
    nxt = CanonicalRole.parse(new)
    if nxt is CanonicalRole.UNKNOWN:
        return False
    if prev is CanonicalRole.UNKNOWN and nxt is not CanonicalRole.UNKNOWN:
        return True
    return prev != nxt
