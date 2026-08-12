"""Fail-safe event hooks: Agent 7 outreach / Agent 6 client / UNKNOWN inbound.

Event-driven only — no global backfill.
Mirror failures never block Agent 6/7 messaging.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


def _ensure_path() -> None:
    root = Path(__file__).resolve().parents[4]
    src = root / "contact_role" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _build_coordinator(store: Any = None) -> Any:
    """Coordinator with live amo adapter when credentials exist; always non-blocking."""
    _ensure_path()
    from contact_role.flags import amo_dry_run, amo_sync_enabled
    from contact_role.state import ContactRoleStore
    from contact_role.sync.amocrm import AmoContactRoleSync
    from contact_role.sync.coordinator import ContactRoleSyncCoordinator

    store_u = store or ContactRoleStore()
    amo_client = None
    try:
        from agent6_qualifier.amo import AmoClient

        client = AmoClient()

        class _Adapter:
            def find_contacts(self, query: str):
                return client.find_contacts(query)

            def get_contact(self, contact_id: int):
                return client.get_contact(contact_id, with_entities="tags")

            def list_contact_custom_fields(self):
                return client.list_contact_custom_fields()

            def update_contact(self, contact_id: int, payload: dict):
                return client.update_contact(contact_id, payload)

        amo_client = _Adapter()
    except Exception:
        amo_client = None

    amo_sync = AmoContactRoleSync(
        amo_client,
        dry_run=amo_dry_run(),
        enabled=amo_sync_enabled(),
    )
    return ContactRoleSyncCoordinator(
        store=store_u,
        amo_sync=amo_sync,
        process_inline_dry_run=False,
        process_amo_async=True,
    )


def on_contact_outreach_started(
    *,
    listing: Any = None,
    phone: str | None = None,
    owner_agent_type: str | None = None,
    tg_username: str | None = None,
    tg_chat_id: str | None = None,
    object_id: str | None = None,
    channel: str | None = None,
    explicit_role: str | None = None,
    contact_id: int | None = None,
    store: Any = None,
    coordinator: Any = None,
    force: bool = False,
    event_at: str | None = None,
    one_shot_guard: Any = None,
    outbound_status: str = "NOT_SENT",
) -> Any:
    """CONTACT_OUTREACH_STARTED — Agent 7 before first outbound message.

    Assigns OWNER/AGENT from Notion type or explicit workflow. Empty → UNKNOWN.
    Never raises. Mirror sync is async and non-blocking.
    """
    return assign_agent7_outreach_role(
        listing=listing,
        phone=phone,
        owner_agent_type=owner_agent_type,
        tg_username=tg_username,
        tg_chat_id=tg_chat_id,
        object_id=object_id,
        channel=channel,
        explicit_role=explicit_role,
        contact_id=contact_id,
        store=store,
        coordinator=coordinator,
        force=force,
        event="CONTACT_OUTREACH_STARTED",
        event_at=event_at,
        one_shot_guard=one_shot_guard,
        outbound_status=outbound_status,
    )


def assign_agent7_outreach_role(
    *,
    listing: Any = None,
    phone: str | None = None,
    owner_agent_type: str | None = None,
    tg_username: str | None = None,
    tg_chat_id: str | None = None,
    object_id: str | None = None,
    channel: str | None = None,
    explicit_role: str | None = None,
    contact_id: int | None = None,
    store: Any = None,
    coordinator: Any = None,
    force: bool = False,
    event: str = "CONTACT_OUTREACH_STARTED",
    event_at: str | None = None,
    one_shot_guard: Any = None,
    outbound_status: str = "NOT_SENT",
) -> Any:
    """Map Notion type → OWNER/AGENT and assign with AGENT7_OUTREACH.

    Notion «Агент/Владелец (тип)»:
      Владелец → OWNER
      Агент → AGENT
      empty → UNKNOWN  (NOT silently OWNER)

    When Notion type is empty, pass ``explicit_role="OWNER"`` or ``"AGENT"``
    only if the workflow context already confirms the recipient type.

    Gated by CONTACT_ROLE_AUTO_ASSIGN_ENABLED (unless force=True for tests).
    One-shot live mode: at most one new OWNER/AGENT event after activation.
    Never raises — returns None on failure / skipped.
    """
    try:
        _ensure_path()
        from contact_role.assign import assign_known_role
        from contact_role.mapping import role_from_notion_owner_agent_type
        from contact_role.one_shot import OneShotLiveGuard, live_auto_assign_gate
        from contact_role.roles import CanonicalRole
        from contact_role.routing_targets import route_for_role
        from contact_role.sources import RoleSource

        allowed, gate_reason = live_auto_assign_gate(
            force=force, agent7_outreach=True
        )
        if not allowed:
            return {
                "applied": False,
                "changed": False,
                "canonical_role": CanonicalRole.UNKNOWN.value,
                "role_source": RoleSource.UNKNOWN.value,
                "route": route_for_role(CanonicalRole.UNKNOWN).value,
                "reason": gate_reason,
                "event": event,
                "role_assigned_before_outbound": False,
            }

        type_value = owner_agent_type
        phone_value = phone
        tg_user = tg_username
        tg_id = tg_chat_id
        oid = object_id or ""
        if listing is not None:
            type_value = type_value if type_value is not None else (
                getattr(listing, "owner_agent_type", "") or ""
            )
            phone_value = phone_value or getattr(listing, "owner_whatsapp", "") or ""
            tg_user = tg_user or (getattr(listing, "owner_telegram", "") or "").lstrip("@")
            oid = oid or getattr(listing, "object_id", "") or ""

        role = role_from_notion_owner_agent_type(type_value)
        if role is CanonicalRole.UNKNOWN and explicit_role:
            role = CanonicalRole.parse(explicit_role)

        if role is CanonicalRole.UNKNOWN:
            return {
                "applied": False,
                "changed": False,
                "canonical_role": CanonicalRole.UNKNOWN.value,
                "role_source": RoleSource.UNKNOWN.value,
                "route": route_for_role(CanonicalRole.UNKNOWN).value,
                "reason": "empty_notion_type_no_explicit_workflow",
                "event": event,
                "role_assigned_before_outbound": False,
            }

        if role is CanonicalRole.CLIENT:
            return {
                "applied": False,
                "changed": False,
                "canonical_role": CanonicalRole.CLIENT.value,
                "role_source": RoleSource.UNKNOWN.value,
                "route": route_for_role(CanonicalRole.CLIENT).value,
                "reason": "client_ignored_for_agent7_outreach",
                "event": event,
                "role_assigned_before_outbound": False,
            }

        guard = one_shot_guard or OneShotLiveGuard()
        gate = guard.evaluate_agent7_event(
            role=role, event_at=event_at, force=force
        )
        if not gate.allowed:
            return {
                "applied": False,
                "changed": False,
                "canonical_role": role.value,
                "role_source": RoleSource.AGENT7_OUTREACH.value,
                "route": route_for_role(role).value,
                "reason": gate.reason,
                "event": event,
                "event_id": gate.event_id,
                "role_assigned_before_outbound": False,
            }

        coord = coordinator
        if coord is None:
            try:
                coord = _build_coordinator(store)
            except Exception:
                coord = None

        result = assign_known_role(
            phone=phone_value,
            tg_username=tg_user,
            tg_chat_id=tg_id,
            role=role,
            source=RoleSource.AGENT7_OUTREACH,
            contact_id=contact_id,
            metadata={
                "object_id": oid,
                "channel": channel or "",
                "notion_owner_agent_type": (type_value or ""),
                "explicit_workflow_role": (explicit_role or ""),
                "event": event,
                "event_id": gate.event_id,
                "role_assigned_before_outbound": True,
            },
            trigger=event,
            store=store,
            coordinator=coord,
        )

        # Conflicting MANUAL lock: do NOT consume one-shot.
        if not result.applied and result.reason == "priority_blocked":
            payload = result.to_dict() if hasattr(result, "to_dict") else {}
            if isinstance(payload, dict):
                payload["event"] = event
                payload["event_id"] = gate.event_id
                payload["reason"] = "priority_blocked"
                payload["one_shot_consumed"] = False
                payload["role_assigned_before_outbound"] = False
                return payload
            return result

        # Accepted OWNER/AGENT assignment (including same-role reuse) consumes one-shot.
        from contact_role.flags import one_shot_live_test_enabled

        if (
            not force
            and one_shot_live_test_enabled()
            and result.applied
            and CanonicalRole.parse(result.canonical_role)
            in {CanonicalRole.OWNER, CanonicalRole.AGENT}
        ):
            sync = result.sync
            amo_status = sync.amocrm_code if sync else "NONE"
            wa_status = sync.whatsapp_code if sync else "NONE"
            report = guard.consume(
                event_id=gate.event_id,
                contact_key=result.contact_key,
                phone=result.phone,
                role=result.canonical_role,
                role_source=result.role_source,
                trigger=event,
                routing=result.route,
                old_role=result.previous_role,
                contact_id=contact_id
                or (
                    (result.state.metadata or {}).get("contact_id")
                    if result.state
                    else None
                ),
                amo_status=amo_status,
                wa_status=wa_status,
                outbound_status=outbound_status,
                role_assigned_before_outbound=True,
                notes=["ROLE_ASSIGNED_BEFORE_OUTBOUND=true"],
            )
            if hasattr(result, "to_dict"):
                # Attach observability without mutating AssignResult dataclass hard.
                result.metadata = dict(getattr(result, "metadata", {}) or {})
                result.metadata["one_shot_report"] = report.to_dict()
                result.metadata["role_assigned_before_outbound"] = True
                result.metadata["one_shot_consumed"] = True
                result.metadata["event_id"] = gate.event_id
            return result

        if hasattr(result, "to_dict"):
            result.metadata = dict(getattr(result, "metadata", {}) or {})
            result.metadata["role_assigned_before_outbound"] = True
            result.metadata["event_id"] = gate.event_id
        return result
    except Exception:
        return None


def on_client_conversation_started(
    *,
    phone: str | None = None,
    tg_username: str | None = None,
    tg_chat_id: str | None = None,
    source: str = "INBOUND_LEAD",
    metadata: dict | None = None,
    store: Any = None,
    coordinator: Any = None,
    force: bool = False,
) -> Any:
    """CLIENT_CONVERSATION_STARTED — confirmed client lead → CLIENT."""
    return assign_known_client_lead(
        phone=phone,
        tg_username=tg_username,
        tg_chat_id=tg_chat_id,
        source=source,
        metadata={**(metadata or {}), "event": "CLIENT_CONVERSATION_STARTED"},
        store=store,
        coordinator=coordinator,
        force=force,
        trigger="CLIENT_CONVERSATION_STARTED",
    )


def assign_known_client_lead(
    *,
    phone: str | None = None,
    tg_username: str | None = None,
    tg_chat_id: str | None = None,
    source: str = "INBOUND_LEAD",
    metadata: dict | None = None,
    store: Any = None,
    coordinator: Any = None,
    force: bool = False,
    trigger: str = "CLIENT_CONVERSATION_STARTED",
) -> Any:
    """Confirmed client lead → CLIENT without waiting for multi-message classify."""
    try:
        _ensure_path()
        from contact_role.assign import assign_known_role
        from contact_role.one_shot import live_auto_assign_gate
        from contact_role.roles import CanonicalRole
        from contact_role.routing_targets import route_for_role
        from contact_role.sources import RoleSource

        allowed, gate_reason = live_auto_assign_gate(
            force=force, agent7_outreach=False
        )
        if not allowed:
            return {
                "applied": False,
                "changed": False,
                "canonical_role": CanonicalRole.CLIENT.value,
                "role_source": RoleSource.parse(source).value,
                "route": route_for_role(CanonicalRole.CLIENT).value,
                "reason": gate_reason,
                "event": "CLIENT_CONVERSATION_STARTED",
            }

        coord = coordinator
        if coord is None:
            try:
                coord = _build_coordinator(store)
            except Exception:
                coord = None

        return assign_known_role(
            phone=phone,
            tg_username=tg_username,
            tg_chat_id=tg_chat_id,
            role=CanonicalRole.CLIENT,
            source=RoleSource.parse(source) if source else RoleSource.INBOUND_LEAD,
            metadata=metadata or {},
            trigger=trigger,
            store=store,
            coordinator=coord,
        )
    except Exception:
        return None


def is_confirmed_client_context(
    *,
    text: str | None = None,
    known_client: bool = False,
    source_hint: str | None = None,
    has_publication_ref: bool = False,
    has_existing_client_session: bool = False,
) -> bool:
    """True when inbound already confirms CLIENT — skip classifier.

    Mirrors Telegram Agent 6 client lead signals:
    object interest + object id / publication link / rental inquiry.
    """
    if known_client or has_publication_ref or has_existing_client_session:
        return True
    hint = (source_hint or "").strip().upper()
    if hint in {
        "INBOUND_LEAD",
        "AGENT6_INBOUND",
        "WORKFLOW_CONTEXT",
        "RENTAL_INQUIRY",
        "CLIENT_CAMPAIGN",
        "LEAD_FORM",
        "OBJECT_INQUIRY",
    }:
        return True

    body = (text or "").strip()
    if not body:
        return False
    low = body.lower()

    # Explicit object-interest phrasing (Agent 6 qualifier style).
    object_interest = (
        "интересует объект",
        "интересует вилл",
        "интересует апартамент",
        "интересует квартир",
        "по объекту",
        "по этому объекту",
        "хочу посмотреть объект",
        "interested in object",
        "interested in the villa",
        "interested in this listing",
    )
    if any(m in low for m in object_interest):
        return True

    # Object ID / publication markers in text.
    try:
        from agent6_qualifier.object_id import (
            extract_object_ids,
            extract_tg_post,
            extract_utm_campaign,
        )

        if extract_object_ids(body) or extract_tg_post(body) or extract_utm_campaign(body):
            return True
    except Exception:
        # Fallback if Agent 6 package path is unavailable.
        if re.search(r"\b(?:[A-Za-z]{1,3}_)?\d{8}_\d{3}\b", body):
            return True
        if "t.me/" in low or "utm_campaign=" in low:
            return True

    # Strong rental-inquiry signals.
    markers = (
        "utm_campaign=",
        "хочу снять",
        "ищу виллу",
        "ищу апартамент",
        "looking for a villa",
        "want to rent",
    )
    return any(m in low for m in markers)

def on_unknown_inbound(
    *,
    phone: str | None,
    text: str | None = None,
    known_client: bool = False,
    source_hint: str | None = None,
    has_publication_ref: bool = False,
    has_existing_client_session: bool = False,
    store: Any = None,
    coordinator: Any = None,
    force: bool = False,
    persist: bool = True,
) -> Any:
    """UNKNOWN inbound → classifier (or CLIENT if known_client lead).

    Never raises. Does not block Agent 6 response path.
    """
    try:
        _ensure_path()
        from contact_role.one_shot import live_auto_assign_gate
        from contact_role.router import route_contact
        from contact_role.roles import CanonicalRole
        from contact_role.sources import RoleSource

        allowed, gate_reason = live_auto_assign_gate(
            force=force, agent7_outreach=False
        )
        if not allowed:
            return {
                "applied": False,
                "reason": gate_reason,
                "canonical_role": CanonicalRole.UNKNOWN.value,
                "route": "CLASSIFIER",
            }

        coord = coordinator
        if coord is None:
            try:
                coord = _build_coordinator(store)
            except Exception:
                coord = None

        if is_confirmed_client_context(
            text=text,
            known_client=known_client,
            source_hint=source_hint,
            has_publication_ref=has_publication_ref,
            has_existing_client_session=has_existing_client_session,
        ):
            return assign_known_client_lead(
                phone=phone,
                source=source_hint or RoleSource.AGENT6_INBOUND.value,
                metadata={"event": "CLIENT_CONVERSATION_STARTED"},
                store=store,
                coordinator=coord,
                force=force,
            )

        decision = route_contact(
            phone=phone,
            text=text,
            store=store,
            coordinator=coord,
            persist_workflow=persist,
            persist_classification=persist,
        )
        return {
            "applied": bool(decision.assign and decision.assign.applied),
            "changed": bool(decision.assign and decision.assign.changed),
            "canonical_role": decision.canonical_role,
            "role_source": decision.role_source,
            "route": decision.route,
            "used_existing": decision.used_existing,
            "used_classifier": decision.used_classifier,
            "notes": list(decision.notes),
            "event": "UNKNOWN_INBOUND",
        }
    except Exception:
        return None
