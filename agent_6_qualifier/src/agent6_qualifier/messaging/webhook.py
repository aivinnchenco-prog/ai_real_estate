"""Wazzup webhook receiver boundary (disabled by default).

CRM API v3: subscription messagesAndStatuses → payload {"messages":[...]}.
Optional Bearer only if WAZZUP_WEBHOOK_BEARER set (not required by Wazzup).
No HMAC in confirmed v3 CRM webhook contract.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from agent6_qualifier.messaging.idempotency import ProcessedEventStore
from agent6_qualifier.messaging.inbound_dry_run import InboundDryRunResult
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    apply_inbound_to_ownership,
)
from agent6_qualifier.messaging.types import CanonicalInboundMessage, ConversationOwner
from agent6_qualifier.messaging.wazzup_config import WazzupConfig, load_wazzup_config
from agent6_qualifier.messaging.wazzup_errors import (
    WazzupMalformedPayload,
    WazzupWebhookDisabled,
)
from agent6_qualifier.messaging.wazzup_inbound import normalize_wazzup_webhook

MAX_BODY_BYTES = 256_000


@dataclass
class WebhookProcessResult:
    accepted: bool
    messages: list[CanonicalInboundMessage]
    duplicates: list[str]
    ignored_reason: str = ""
    auto_reply: bool = False
    is_test_ping: bool = False
    dry_runs: list[InboundDryRunResult] = field(default_factory=list)
    turns: list[Any] = field(default_factory=list)


def validate_webhook_auth(
    headers: dict[str, str],
    config: WazzupConfig,
) -> None:
    """If WAZZUP_WEBHOOK_BEARER set, require Authorization: Bearer <value>.

    Wazzup CRM v3 webhooks do not document a required custom auth header —
    leave WAZZUP_WEBHOOK_BEARER empty for real Wazzup delivery.
    """
    expected = (config.webhook_bearer or "").strip()
    if not expected:
        return
    auth = ""
    for key, value in headers.items():
        if key.lower() == "authorization":
            auth = value.strip()
            break
    if auth != f"Bearer {expected}":
        raise PermissionError("WAZZUP_WEBHOOK_AUTH_FAILED")


def _parse_body(body: bytes | dict[str, Any]) -> dict[str, Any]:
    if isinstance(body, dict):
        return body
    if isinstance(body, (bytes, bytearray)):
        if len(body) > MAX_BODY_BYTES:
            raise WazzupMalformedPayload("webhook body too large")
        try:
            payload = json.loads(body.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WazzupMalformedPayload(f"invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise WazzupMalformedPayload("webhook body must be a JSON object")
        return payload
    raise WazzupMalformedPayload("webhook body must be a JSON object")


def _is_status_only(msg: CanonicalInboundMessage) -> bool:
    raw = (msg.raw_event_type or "").lower()
    return "status" in raw and not (msg.text or "").strip()


def _run_shared_client_turn(
    msg: CanonicalInboundMessage,
    *,
    cfg: WazzupConfig,
    ownership_by_chat: dict[str, ConversationOwnershipState],
    store: ProcessedEventStore | None,
    amo: Any | None,
    qualifier: Any | None,
    force_dry: bool | None,
) -> Any:
    from agent6_qualifier.messaging.wa_client_runtime import (
        default_session_store,
        process_whatsapp_client_turn,
    )

    turn = asyncio.run(
        process_whatsapp_client_turn(
            msg,
            config=cfg,
            ownership=ownership_by_chat[msg.chat_id],
            event_store=store,
            amo=amo,
            qualifier=qualifier,
            force_dry_run_send=force_dry,
        )
    )
    print("\n".join(turn.to_log_lines()), flush=True)
    return turn


def flush_debounced_client_turns(
    debounce,
    *,
    config: WazzupConfig,
    store: ProcessedEventStore | None = None,
    ownership_by_chat: dict[str, ConversationOwnershipState] | None = None,
    amo: Any | None = None,
    qualifier: Any | None = None,
    replied: bool = False,
) -> list[Any]:
    """Process coalesced chats whose debounce window has elapsed."""
    from agent6_qualifier.messaging.inbound_debounce import coalesce_messages

    ownership_by_chat = ownership_by_chat if ownership_by_chat is not None else {}
    turns: list[Any] = []
    for group in debounce.pop_ready():
        coal = coalesce_messages(group)
        if coal.chat_id not in ownership_by_chat:
            ownership_by_chat[coal.chat_id] = ConversationOwnershipState(
                chat_id=coal.chat_id
            )
        force_dry = True if (config.stage_mode and replied) else None
        turn = _run_shared_client_turn(
            coal,
            cfg=config,
            ownership_by_chat=ownership_by_chat,
            store=store,
            amo=amo,
            qualifier=qualifier,
            force_dry=force_dry,
        )
        turns.append(turn)
        if getattr(turn, "outbound_mode", "") == "live":
            replied = True
    return turns


def process_wazzup_webhook(
    body: bytes | dict[str, Any],
    *,
    config: WazzupConfig | None = None,
    store: ProcessedEventStore | None = None,
    ownership_by_chat: dict[str, ConversationOwnershipState] | None = None,
    headers: dict[str, str] | None = None,
    on_inbound: Callable[[CanonicalInboundMessage], None] | None = None,
    run_qualification_dry_run: bool = True,
    use_shared_core: bool = True,
    amo: Any | None = None,
    qualifier: Any | None = None,
    debounce: Any | None = None,
    defer_client_turns: bool = False,
) -> WebhookProcessResult:
    cfg = config or load_wazzup_config()
    if not cfg.webhook_enabled:
        raise WazzupWebhookDisabled("WAZZUP_WEBHOOK_ENABLED=false")

    validate_webhook_auth(headers or {}, cfg)
    payload = _parse_body(body)

    # Wazzup PATCH /v3/webhooks verification: POST {"test": true}
    if payload.get("test") is True and "messages" not in payload:
        return WebhookProcessResult(
            accepted=True,
            messages=[],
            duplicates=[],
            ignored_reason="",
            auto_reply=False,
            is_test_ping=True,
        )

    try:
        messages = normalize_wazzup_webhook(payload)
    except WazzupMalformedPayload:
        raise
    except Exception as exc:  # noqa: BLE001
        raise WazzupMalformedPayload(str(exc)) from exc

    if not messages:
        return WebhookProcessResult(
            accepted=True,
            messages=[],
            duplicates=[],
            ignored_reason="unsupported or empty event",
            auto_reply=False,
        )

    ownership_by_chat = ownership_by_chat if ownership_by_chat is not None else {}
    duplicates: list[str] = []
    accepted: list[CanonicalInboundMessage] = []
    dry_runs: list[InboundDryRunResult] = []
    turns: list[Any] = []
    now = datetime.now(timezone.utc).isoformat()
    replied = False

    for msg in messages:
        if msg.channel_id and msg.channel_id != cfg.channel_id:
            continue
        if _is_status_only(msg):
            continue
        if store is not None and not store.mark_inbound(msg.message_id, seen_at=now):
            duplicates.append(msg.message_id)
            continue
        state = ownership_by_chat.get(msg.chat_id) or ConversationOwnershipState(
            chat_id=msg.chat_id
        )
        ownership_by_chat[msg.chat_id] = apply_inbound_to_ownership(state, msg)
        stopped = ownership_by_chat[msg.chat_id]
        if msg.direction == "outbound" and (
            stopped.manager_takeover or stopped.owner == ConversationOwner.HUMAN_HANDOFF
        ):
            try:
                from agent6_qualifier.handoff_control import (
                    activate_stop,
                    persist_ownership,
                )
                from agent6_qualifier.messaging.wa_client_runtime import (
                    default_session_store,
                    whatsapp_session_chat_id,
                )

                persist_ownership(stopped, phone=msg.phone or "")
                session_store = default_session_store()
                cid = whatsapp_session_chat_id(msg.phone, chat_id=msg.chat_id)
                sess = session_store.load(cid)
                if sess is None:
                    from agent6_qualifier.qualifier import Session

                    sess = Session(chat_id=cid)
                activate_stop(
                    sess,
                    stopped,
                    reason=stopped.reason or "C: chat",
                    lead_id=sess.amo_lead_id,
                )
                session_store.save(sess)
            except Exception:
                pass
        accepted.append(msg)
        if on_inbound is not None:
            on_inbound(msg)

        # Outbound echo / human outbound: ownership updated; do not qualify.
        if msg.direction == "outbound":
            continue

        if use_shared_core:
            from agent6_qualifier.messaging.wa_client_runtime import (
                default_session_store,
            )
            from agent6_qualifier.messaging.wa_owner_runtime import (
                is_whatsapp_owner_inbound,
                process_whatsapp_owner_turn,
            )

            session_store = default_session_store()
            if is_whatsapp_owner_inbound(msg, session_store):
                owner_turn = asyncio.run(
                    process_whatsapp_owner_turn(
                        msg,
                        config=cfg,
                        ownership=ownership_by_chat[msg.chat_id],
                        store=session_store,
                        event_store=store,
                        amo=amo,
                    )
                )
                turns.append(owner_turn)
                print("\n".join(owner_turn.to_log_lines()), flush=True)
                try:
                    from agent6_qualifier.messaging.e2e_report import record_owner_turn

                    record_owner_turn(turn=owner_turn, phone=msg.phone)
                except Exception:
                    pass
                continue

            if debounce is not None:
                debounce.add(msg)
                continue

            # Stage mode: at most one bot reply per webhook batch.
            force_dry = None
            if cfg.stage_mode and replied:
                force_dry = True

            turn = _run_shared_client_turn(
                msg,
                cfg=cfg,
                ownership_by_chat=ownership_by_chat,
                store=store,
                amo=amo,
                qualifier=qualifier,
                force_dry=force_dry,
            )
            turns.append(turn)
            if turn.outbound_mode == "live":
                replied = True
            continue

        if run_qualification_dry_run:
            from agent6_qualifier.messaging.inbound_dry_run import (
                run_whatsapp_inbound_dry_run,
            )

            dry = run_whatsapp_inbound_dry_run(
                msg,
                config=cfg,
                ownership=ownership_by_chat[msg.chat_id],
                amo=amo,
                qualifier=qualifier,
            )
            dry_runs.append(dry)
            print("\n".join(dry.to_log_lines()), flush=True)

    if debounce is not None and not defer_client_turns:
        wait = debounce.min_remaining()
        if wait:
            time.sleep(wait)
        turns.extend(
            flush_debounced_client_turns(
                debounce,
                config=cfg,
                store=store,
                ownership_by_chat=ownership_by_chat,
                amo=amo,
                qualifier=qualifier,
                replied=replied,
            )
        )

    ignored = ""
    if not accepted and not duplicates:
        ignored = "wrong channel or empty after filter"

    return WebhookProcessResult(
        accepted=True,
        messages=accepted,
        duplicates=duplicates,
        ignored_reason=ignored,
        auto_reply=bool(cfg.auto_reply_enabled and cfg.send_enabled),
        dry_runs=dry_runs,
        turns=turns,
    )
