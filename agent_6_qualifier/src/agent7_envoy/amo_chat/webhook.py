"""amoCRM Chat API webhook → source-native owner send (with loop guards)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from agent7_envoy.amo_chat.client import AmojoChatClient
from agent7_envoy.amo_chat.config import AmoChatConfig, load_amo_chat_config
from agent7_envoy.amo_chat.events import amo_chat_event
from agent7_envoy.amo_chat.identity import resolve_channel_key
from agent7_envoy.amo_chat.mirror_store import AmoChatMirrorStore
from agent7_envoy.amo_chat.origin import (
    MessageOrigin,
    classify_amo_webhook_message,
    should_send_to_source,
)
from agent7_envoy.amo_chat.signing import verify_signature

# Soft cap for manager→source text (bytes of unicode codepoints).
MAX_MANAGER_TEXT_CHARS = 4000


@dataclass
class WebhookHandleResult:
    ok: bool
    origin: str = ""
    action: str = ""  # ignored | sent | failed | degraded
    reason: str = ""
    conversation_id: str = ""
    msgid: str = ""
    notes: list[str] = field(default_factory=list)


SendToSourceFn = Callable[..., Any]


def parse_amo_chat_webhook(body: bytes | str | dict) -> dict[str, Any]:
    if isinstance(body, dict):
        return body
    raw = body.encode("utf-8") if isinstance(body, str) else body
    data = json.loads(raw.decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {}


def extract_new_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Chat API v2 webhook shapes to a flat message dict."""
    if "new_message" in payload and isinstance(payload["new_message"], dict):
        return payload["new_message"]
    if "message" in payload and isinstance(payload["message"], dict):
        # Sometimes nested under event
        msg = payload["message"]
        if "text" in msg or "message" in msg:
            return {
                "conversation_id": payload.get("conversation_id")
                or payload.get("conversation", {}).get("id")
                if isinstance(payload.get("conversation"), dict)
                else payload.get("conversation_id"),
                "msgid": payload.get("msgid") or msg.get("id") or msg.get("msgid"),
                "text": msg.get("text")
                or (msg.get("message") or {}).get("text")
                if isinstance(msg.get("message"), dict)
                else msg.get("text"),
                "sender": payload.get("sender") or msg.get("sender") or {},
                "receiver": payload.get("receiver") or msg.get("receiver"),
            }
    # Flat form
    return {
        "conversation_id": payload.get("conversation_id") or "",
        "msgid": payload.get("msgid") or "",
        "text": payload.get("text")
        or (
            (payload.get("message") or {}).get("text")
            if isinstance(payload.get("message"), dict)
            else ""
        ),
        "sender": payload.get("sender") or {},
        "receiver": payload.get("receiver"),
    }


class AmoChatWebhookHandler:
    def __init__(
        self,
        config: AmoChatConfig | None = None,
        *,
        store: AmoChatMirrorStore | None = None,
        client: AmojoChatClient | None = None,
        send_to_source: SendToSourceFn | None = None,
        dry_run: bool = True,
    ):
        self.config = config or load_amo_chat_config()
        self.store = store or AmoChatMirrorStore()
        self.client = client or AmojoChatClient(self.config, dry_run=dry_run)
        self.send_to_source = send_to_source
        self.dry_run = dry_run

    def verify(
        self,
        *,
        channel_key: str,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
    ) -> bool:
        try:
            ch = self.config.channel(channel_key)
        except KeyError:
            return False
        return verify_signature(
            method=method,
            body=body,
            path=path,
            secret=ch.channel_secret,
            headers=headers,
        )

    def handle(
        self,
        *,
        channel_key: str,
        body: bytes | str | dict,
        headers: dict[str, str] | None = None,
        path: str = "/webhooks/amo-chat",
        skip_verify: bool = False,
    ) -> WebhookHandleResult:
        if not self.config.webhook_enabled and not skip_verify:
            amo_chat_event("AMO_CHAT_WEBHOOK_DISABLED", channel=channel_key)
            return WebhookHandleResult(
                ok=False, action="ignored", reason="webhook_disabled"
            )

        raw = (
            body
            if isinstance(body, (bytes, bytearray))
            else (
                json.dumps(body, ensure_ascii=False).encode("utf-8")
                if isinstance(body, dict)
                else str(body).encode("utf-8")
            )
        )
        if not skip_verify:
            if not self.verify(
                channel_key=channel_key,
                method="POST",
                path=path,
                body=bytes(raw),
                headers=headers or {},
            ):
                amo_chat_event(
                    "AMO_CHAT_WEBHOOK_SIGNATURE_INVALID", channel=channel_key
                )
                return WebhookHandleResult(
                    ok=False, action="ignored", reason="signature_invalid"
                )

        payload = parse_amo_chat_webhook(body if isinstance(body, dict) else raw)
        msg = extract_new_message(payload)
        conversation_id = str(msg.get("conversation_id") or "")
        msgid = str(msg.get("msgid") or "")
        text = str(msg.get("text") or "")
        if len(text) > MAX_MANAGER_TEXT_CHARS:
            text = text[:MAX_MANAGER_TEXT_CHARS]
        sender = msg.get("sender") if isinstance(msg.get("sender"), dict) else {}
        receiver = msg.get("receiver")

        amo_chat_event(
            "AMO_CHAT_WEBHOOK_RECEIVED",
            channel=channel_key,
            conversation_id=conversation_id,
            msgid=msgid,
            text_len=len(text),
        )

        rec = self.store.get_by_conversation(conversation_id)
        already = bool(
            rec and msgid and msgid in rec.imported_msgids
        ) or msgid.startswith("oh-")

        bot_ids = set()
        try:
            ch_cfg = self.config.channel(channel_key)
            if ch_cfg.bot_id:
                bot_ids.add(ch_cfg.bot_id)
        except KeyError:
            pass
        sender_id = str(sender.get("id") or sender.get("ref_id") or "")
        sender_is_bot = sender_id in bot_ids or str(sender.get("name") or "").startswith(
            "Open Home"
        )

        origin = classify_amo_webhook_message(
            msgid=msgid,
            conversation_id=conversation_id,
            already_imported=already,
            sender_is_bot=sender_is_bot,
            has_receiver=receiver is not None,
        )

        if not should_send_to_source(origin):
            event = (
                "AMO_CHAT_WEBHOOK_DUPLICATE"
                if already
                else "AMO_CHAT_LOOP_BLOCKED"
            )
            amo_chat_event(
                event,
                channel=channel_key,
                conversation_id=conversation_id,
                msgid=msgid,
                origin=origin.value,
            )
            return WebhookHandleResult(
                ok=True,
                origin=origin.value,
                action="ignored",
                reason="loop_guard_or_echo",
                conversation_id=conversation_id,
                msgid=msgid,
            )

        if rec is None:
            return WebhookHandleResult(
                ok=False,
                origin=origin.value,
                action="failed",
                reason="EXTERNAL_THREAD_UNAVAILABLE",
                conversation_id=conversation_id,
                msgid=msgid,
            )
        if not rec.external_thread_id:
            return WebhookHandleResult(
                ok=False,
                origin=origin.value,
                action="failed",
                reason="EXTERNAL_THREAD_UNAVAILABLE",
                conversation_id=conversation_id,
                msgid=msgid,
            )

        # Human intervention on owner thread
        self.store.set_ownership(conversation_id, "HUMAN_ACTIVE")
        amo_chat_event(
            "AMO_CHAT_MANAGER_OUTBOUND",
            channel=channel_key,
            conversation_id=conversation_id,
            msgid=msgid,
            owner_request_id=rec.owner_request_id,
        )

        if self.send_to_source is None or self.dry_run:
            return WebhookHandleResult(
                ok=True,
                origin=origin.value,
                action="sent" if not self.dry_run else "ignored",
                reason="dry_run_or_no_transport",
                conversation_id=conversation_id,
                msgid=msgid,
                notes=["would_send_to_source"],
            )

        # Claim msgid before external side-effect so webhook replay cannot double-send.
        if msgid:
            self.store.mark_imported(
                conversation_id,
                msgid,
                direction="outbound",
                external_message_id=msgid,
            )

        try:
            result = self.send_to_source(
                channel=resolve_channel_key(channel_key),
                external_thread_id=rec.external_thread_id,
                text=text,
                owner_request_id=rec.owner_request_id,
                source_url=rec.source_url,
                object_id=rec.object_id,
            )
        except Exception as exc:
            self._report_failure(channel_key, msgid, "SEND_FAILED", repr(exc))
            amo_chat_event(
                "FB_EXTERNAL_SEND" if "face" in channel_key else "AIRBNB_EXTERNAL_SEND",
                channel=channel_key,
                msgid=msgid,
                outcome="SEND_FAILED",
            )
            return WebhookHandleResult(
                ok=False,
                origin=origin.value,
                action="failed",
                reason="SEND_FAILED",
                conversation_id=conversation_id,
                msgid=msgid,
                notes=[type(exc).__name__],
            )

        outcome = str(getattr(result, "outcome", result) or "")
        if "AUTH" in outcome.upper():
            code = "AUTH_REQUIRED"
        elif "RATE" in outcome.upper():
            code = "RATE_LIMITED"
        elif "THREAD" in outcome.upper() or "CONTEXT" in outcome.upper():
            code = "THREAD_UNAVAILABLE"
        elif "SENT" in outcome.upper() or outcome == "ok":
            code = ""
        else:
            code = "SEND_FAILED"

        if code:
            self._report_failure(channel_key, msgid, code, outcome)
            amo_chat_event(
                "FB_EXTERNAL_SEND" if "face" in channel_key else "AIRBNB_EXTERNAL_SEND",
                channel=channel_key,
                msgid=msgid,
                outcome=code,
            )
            return WebhookHandleResult(
                ok=False,
                origin=origin.value,
                action="failed",
                reason=code,
                conversation_id=conversation_id,
                msgid=msgid,
            )

        amo_chat_event(
            "FB_EXTERNAL_SEND" if "face" in channel_key else "AIRBNB_EXTERNAL_SEND",
            channel=channel_key,
            msgid=msgid,
            outcome="SENT",
        )
        return WebhookHandleResult(
            ok=True,
            origin=origin.value,
            action="sent",
            conversation_id=conversation_id,
            msgid=msgid,
        )

    def _report_failure(
        self, channel_key: str, msgid: str, error_code: str, error: str
    ) -> None:
        if not msgid:
            return
        try:
            ch = self.config.channel(channel_key)
            # Chat API delivery_status: non-zero / error fields indicate failure
            self.client.set_delivery_status(
                ch,
                msgid,
                delivery_status=-1,
                error_code=error_code,
                error=error[:200],
            )
        except Exception:
            pass
