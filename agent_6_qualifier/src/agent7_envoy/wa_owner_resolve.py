"""WhatsApp owner inbound session resolution (request-specific, unambiguous)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from agent6_qualifier.messaging.wazzup_config import normalize_phone_e164_digits
from agent6_qualifier.qualifier import Session
from agent6_qualifier.sessions import SessionStore
from agent7_envoy.owner_request_store import OwnerRequest, OwnerRequestStore

ResolveCode = Literal[
    "OK",
    "OWNER_REQUEST_NOT_FOUND",
    "OWNER_REQUEST_AMBIGUOUS",
    "OWNER_REPLY_DUPLICATE",
]


@dataclass
class WaOwnerResolveResult:
    code: ResolveCode
    session: Session | None = None
    request: OwnerRequest | None = None
    reason: str = ""


def resolve_whatsapp_owner_session(
    *,
    owner_phone: str,
    session_store: SessionStore,
    request_store: OwnerRequestStore,
    registry_entry: dict[str, Any] | None = None,
    message_id: str = "",
) -> WaOwnerResolveResult:
    """Resolve exactly one client session for a WA owner inbound.

    Order:
      1. explicit open OwnerRequest for owner phone (request-specific)
      2. registry owner_request_id / object_id (unambiguous only)
      3. SessionStore awaiting-by-whatsapp (exactly one)
    """
    digits = normalize_phone_e164_digits(owner_phone) or ""
    if not digits:
        return WaOwnerResolveResult(
            code="OWNER_REQUEST_NOT_FOUND", reason="empty_owner_phone"
        )

    if message_id:
        prior = request_store.find_by_processed_message(digits, message_id)
        if prior is not None:
            return WaOwnerResolveResult(
                code="OWNER_REPLY_DUPLICATE",
                request=prior,
                reason="duplicate_owner_message_id",
            )

    open_reqs = request_store.list_open_for_owner(digits)
    if len(open_reqs) > 1:
        return WaOwnerResolveResult(
            code="OWNER_REQUEST_AMBIGUOUS",
            reason="multiple_open_owner_requests",
        )
    if len(open_reqs) == 1:
        req = open_reqs[0]
        if message_id and request_store.already_processed(
            req.owner_request_id, message_id
        ):
            return WaOwnerResolveResult(
                code="OWNER_REPLY_DUPLICATE",
                request=req,
                reason="duplicate_owner_message_id",
            )
        session = session_store.load(req.client_session_chat_id)
        if session is None:
            return WaOwnerResolveResult(
                code="OWNER_REQUEST_NOT_FOUND",
                request=req,
                reason="client_session_missing",
            )
        return WaOwnerResolveResult(code="OK", session=session, request=req)

    # Registry fallbacks (still must be unambiguous).
    if registry_entry:
        rid = str(registry_entry.get("owner_request_id") or "").strip()
        if rid:
            req = request_store.get(rid)
            if req is not None:
                if message_id and request_store.already_processed(rid, message_id):
                    return WaOwnerResolveResult(
                        code="OWNER_REPLY_DUPLICATE",
                        request=req,
                        reason="duplicate_owner_message_id",
                    )
                session = session_store.load(req.client_session_chat_id)
                if session is not None:
                    return WaOwnerResolveResult(
                        code="OK", session=session, request=req
                    )
        object_id = str(registry_entry.get("object_id") or "").strip()
        if object_id:
            matches = session_store.list_awaiting_owner_by_object(object_id)
            if len(matches) > 1:
                return WaOwnerResolveResult(
                    code="OWNER_REQUEST_AMBIGUOUS",
                    reason="multiple_awaiting_same_object",
                )
            if len(matches) == 1:
                return WaOwnerResolveResult(code="OK", session=matches[0])

    wa_matches = session_store.list_awaiting_owner_by_whatsapp(digits)
    if len(wa_matches) > 1:
        return WaOwnerResolveResult(
            code="OWNER_REQUEST_AMBIGUOUS",
            reason="multiple_awaiting_same_owner_whatsapp",
        )
    if len(wa_matches) == 1:
        return WaOwnerResolveResult(code="OK", session=wa_matches[0])

    return WaOwnerResolveResult(
        code="OWNER_REQUEST_NOT_FOUND", reason="no_awaiting_session"
    )
