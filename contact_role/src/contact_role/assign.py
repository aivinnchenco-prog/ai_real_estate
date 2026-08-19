"""assign_known_role — deterministic known-workflow role API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .audit import AuditLog
from .phone import contact_key_for, normalize_phone_e164
from .policy import can_overwrite_role
from .roles import CanonicalRole
from .routing_targets import RouteTarget, route_for_role
from .sources import RoleSource
from .state import ContactRoleState, ContactRoleStore
from .sync.coordinator import ContactRoleSyncCoordinator, SyncFanOutResult


@dataclass
class AssignResult:
    applied: bool
    changed: bool
    contact_key: str
    phone: str
    canonical_role: str
    role_source: str
    previous_role: str
    previous_source: str
    route: str
    reason: str = ""
    sync: SyncFanOutResult | None = None
    state: ContactRoleState | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "changed": self.changed,
            "contact_key": self.contact_key,
            "phone": self.phone,
            "canonical_role": self.canonical_role,
            "role_source": self.role_source,
            "previous_role": self.previous_role,
            "previous_source": self.previous_source,
            "route": self.route,
            "reason": self.reason,
            "sync": self.sync.to_dict() if self.sync else None,
            "metadata": dict(self.metadata) if self.metadata else None,
        }


def assign_known_role(
    *,
    phone: str | None = None,
    role: CanonicalRole | str,
    source: RoleSource | str,
    contact_key: str | None = None,
    tg_username: str | None = None,
    tg_chat_id: str | None = None,
    contact_id: int | None = None,
    confidence: float | None = None,
    metadata: dict[str, Any] | None = None,
    trigger: str = "assign_known_role",
    allow_reevaluation: bool = False,
    store: ContactRoleStore | None = None,
    coordinator: ContactRoleSyncCoordinator | None = None,
    audit: AuditLog | None = None,
    enqueue_sync: bool = True,
) -> AssignResult:
    """Persist known workflow role and fan-out dual sync when changed.

    Steps: normalize → priority check → persist → audit → enqueue amo + WA.
    Never raises into callers when used via fail-safe wrappers.
    """
    role_n = CanonicalRole.parse(role)
    source_n = RoleSource.parse(source)
    phone_n = normalize_phone_e164(phone) or ""
    key = contact_key_for(
        phone=phone_n or phone,
        contact_key=contact_key,
        tg_username=tg_username,
        tg_chat_id=tg_chat_id,
    )
    if not key:
        return AssignResult(
            applied=False,
            changed=False,
            contact_key="",
            phone=phone_n,
            canonical_role=role_n.value,
            role_source=source_n.value,
            previous_role=CanonicalRole.UNKNOWN.value,
            previous_source=RoleSource.UNKNOWN.value,
            route=RouteTarget.CLASSIFIER.value,
            reason="missing_identity",
        )

    store_u = store or ContactRoleStore()
    current = store_u.get(key) or (
        store_u.get_by_phone(phone_n) if phone_n else None
    )
    prev_role = (
        CanonicalRole.parse(current.canonical_role)
        if current
        else CanonicalRole.UNKNOWN
    )
    prev_source = (
        RoleSource.parse(current.role_source) if current else RoleSource.UNKNOWN
    )

    if not can_overwrite_role(
        current,
        new_role=role_n,
        new_source=source_n,
        allow_reevaluation=allow_reevaluation,
    ):
        route = route_for_role(prev_role).value
        audit_u = audit or AuditLog()
        audit_u.emit_assignment_rejected(
            contact_key=key,
            phone=phone_n or (current.phone if current else ""),
            old_role=prev_role,
            candidate_role=role_n,
            role_source=source_n,
            previous_source=prev_source,
            trigger=trigger,
            reason="priority_blocked",
            contact_id=contact_id,
            priority=(
                "manual_lock"
                if current and current.locked_by_manual_override
                else "priority_blocked"
            ),
        )
        return AssignResult(
            applied=False,
            changed=False,
            contact_key=key,
            phone=phone_n or (current.phone if current else ""),
            canonical_role=prev_role.value,
            role_source=prev_source.value,
            previous_role=prev_role.value,
            previous_source=prev_source.value,
            route=route,
            reason="priority_blocked",
            state=current,
        )

    changed = prev_role != role_n or current is None
    same_role_refresh = prev_role == role_n and current is not None

    meta = dict(current.metadata) if current else {}
    if metadata:
        meta.update(metadata)

    state = ContactRoleState(
        contact_key=key,
        phone=phone_n or (current.phone if current else ""),
        canonical_role=role_n.value,
        role_source=source_n.value,
        role_confidence=confidence,
        role_set_at=(current.role_set_at if current and same_role_refresh else ""),
        locked_by_manual_override=(source_n is RoleSource.MANUAL),
        metadata=meta,
    )
    if contact_id is not None:
        state.metadata["contact_id"] = contact_id

    store_u.upsert(state)

    if changed:
        (audit or AuditLog()).emit_role_changed(
            contact_key=key,
            phone=state.phone,
            old_role=prev_role,
            new_role=role_n,
            role_source=source_n,
            previous_source=prev_source,
            trigger=trigger,
            contact_id=contact_id,
        )

    sync_result: SyncFanOutResult | None = None
    if enqueue_sync and changed:
        try:
            coord = coordinator or ContactRoleSyncCoordinator(store=store_u)
            sync_result = coord.fan_out(
                state,
                previous_role=prev_role,
                force=False,
            )
        except Exception as exc:  # noqa: BLE001 — mirrors must never block routing
            sync_result = SyncFanOutResult(
                amocrm_code="FAILED",
                whatsapp_code="FAILED",
                amocrm_error=str(exc)[:200],
                whatsapp_error=str(exc)[:200],
                notes=["mirror fan_out isolated failure — canonical role kept"],
            )
    elif enqueue_sync and same_role_refresh:
        sync_result = SyncFanOutResult(
            amocrm_code="ALREADY_SYNCED",
            whatsapp_code="ALREADY_SYNCED",
            notes=["same role — no duplicate sync jobs"],
        )

    return AssignResult(
        applied=True,
        changed=changed,
        contact_key=key,
        phone=state.phone,
        canonical_role=role_n.value,
        role_source=source_n.value,
        previous_role=prev_role.value,
        previous_source=prev_source.value,
        route=route_for_role(role_n).value,
        reason="ok" if changed else "already_same_role",
        sync=sync_result,
        state=state,
    )


def unlock_manual_override(
    *,
    phone: str | None = None,
    contact_key: str | None = None,
    store: ContactRoleStore | None = None,
) -> ContactRoleState | None:
    """Clear manual lock so automatic sources may update again."""
    store_u = store or ContactRoleStore()
    key = contact_key_for(phone=phone, contact_key=contact_key)
    if not key:
        return None
    state = store_u.get(key) or store_u.get_by_phone(phone)
    if state is None:
        return None
    state.locked_by_manual_override = False
    if RoleSource.parse(state.role_source) is RoleSource.MANUAL:
        # Keep MANUAL source but unlock for future higher/equal workflow writes
        # that pass allow_reevaluation or higher priority.
        pass
    return store_u.upsert(state)
