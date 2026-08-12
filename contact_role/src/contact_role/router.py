"""WhatsApp / workflow router: canonical role → Agent 6 / Agent 7 / classifier."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .assign import AssignResult, assign_known_role
from .classify import ClassificationResult, classify_unknown_contact_role
from .roles import CanonicalRole
from .routing_targets import RouteTarget, route_for_role
from .sources import RoleSource
from .state import ContactRoleState, ContactRoleStore

__all__ = ["RouteTarget", "RoutingDecision", "route_contact", "route_for_role"]


@dataclass
class RoutingDecision:
    route: str
    canonical_role: str
    role_source: str
    used_existing: bool = False
    used_workflow: bool = False
    used_classifier: bool = False
    assign: AssignResult | None = None
    classification: ClassificationResult | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "canonical_role": self.canonical_role,
            "role_source": self.role_source,
            "used_existing": self.used_existing,
            "used_workflow": self.used_workflow,
            "used_classifier": self.used_classifier,
            "notes": list(self.notes),
        }


def route_contact(
    *,
    phone: str | None = None,
    text: str | None = None,
    contact_key: str | None = None,
    known_role: CanonicalRole | str | None = None,
    known_source: RoleSource | str | None = None,
    store: ContactRoleStore | None = None,
    coordinator: Any = None,
    persist_workflow: bool = True,
    persist_classification: bool = True,
) -> RoutingDecision:
    """Shared router before Agent 6 / Agent 7.

    Priority:
      1) existing confirmed canonical role
      2) known workflow role (assign immediately)
      3) message classification when UNKNOWN
    """
    store_u = store or ContactRoleStore()
    existing: ContactRoleState | None = None
    if contact_key:
        existing = store_u.get(contact_key)
    if existing is None and phone:
        existing = store_u.get_by_phone(phone)

    if existing and CanonicalRole.parse(existing.canonical_role) is not CanonicalRole.UNKNOWN:
        role = CanonicalRole.parse(existing.canonical_role)
        return RoutingDecision(
            route=route_for_role(role).value,
            canonical_role=role.value,
            role_source=existing.role_source,
            used_existing=True,
            notes=["existing confirmed canonical role"],
        )

    if known_role is not None and CanonicalRole.parse(known_role) is not CanonicalRole.UNKNOWN:
        source = RoleSource.parse(known_source or RoleSource.WORKFLOW_CONTEXT)
        assign: AssignResult | None = None
        if persist_workflow:
            assign = assign_known_role(
                phone=phone,
                contact_key=contact_key,
                role=known_role,
                source=source,
                store=store_u,
                coordinator=coordinator,
                trigger="route_contact.workflow",
            )
            role = CanonicalRole.parse(assign.canonical_role)
            return RoutingDecision(
                route=assign.route,
                canonical_role=role.value,
                role_source=assign.role_source,
                used_workflow=True,
                assign=assign,
                notes=["known workflow role assigned"],
            )
        role = CanonicalRole.parse(known_role)
        return RoutingDecision(
            route=route_for_role(role).value,
            canonical_role=role.value,
            role_source=source.value,
            used_workflow=True,
            notes=["known workflow role (not persisted)"],
        )

    classification = classify_unknown_contact_role(
        phone=phone,
        contact_key=contact_key,
        text=text,
        store=store_u,
        persist=persist_classification,
        coordinator=coordinator,
    )
    role = CanonicalRole.parse(classification.candidate_role)
    if classification.applied and classification.applied.applied:
        return RoutingDecision(
            route=classification.applied.route,
            canonical_role=classification.applied.canonical_role,
            role_source=classification.applied.role_source,
            used_classifier=True,
            classification=classification,
            assign=classification.applied,
            notes=["classified UNKNOWN contact"],
        )
    return RoutingDecision(
        route=route_for_role(role).value,
        canonical_role=role.value,
        role_source=RoleSource.MESSAGE_CLASSIFICATION.value
        if role is not CanonicalRole.UNKNOWN
        else RoleSource.UNKNOWN.value,
        used_classifier=True,
        classification=classification,
        notes=["classifier path"],
    )
