"""Source-native ↔ amoCRM custom chat mirror (secondary interface)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent7_envoy.amo_chat.client import AmojoChatClient, AmojoResponse
from agent7_envoy.amo_chat.config import AmoChatConfig, load_amo_chat_config
from agent7_envoy.amo_chat.identity import (
    resolve_channel_key,
    stable_conversation_id,
    stable_message_id,
    text_fingerprint,
)
from agent7_envoy.amo_chat.mirror_store import AmoChatMirrorRecord, AmoChatMirrorStore
from agent7_envoy.amo_chat.origin import MessageOrigin, should_mirror_to_amo
from agent7_envoy.amo_chat.payloads import (
    build_inbound_owner_payload,
    build_outbound_bot_payload,
)


@dataclass
class MirrorResult:
    ok: bool
    degraded: bool = False
    skipped: bool = False
    reason: str = ""
    conversation_id: str = ""
    msgid: str = ""
    notes: list[str] = field(default_factory=list)


class AmoChatMirrorService:
    """Mirrors FB/Airbnb transcripts into amo custom channels.

    Never blocks Agent7 business flow — failures → AMO_CHAT_MIRROR_DEGRADED.
    """

    def __init__(
        self,
        config: AmoChatConfig | None = None,
        *,
        store: AmoChatMirrorStore | None = None,
        client: AmojoChatClient | None = None,
        dry_run: bool = True,
    ):
        self.config = config or load_amo_chat_config()
        self.store = store or AmoChatMirrorStore()
        self.dry_run = dry_run
        self.client = client or AmojoChatClient(self.config, dry_run=dry_run)

    def ensure_record(
        self,
        *,
        channel: str,
        owner_request_id: str,
        object_id: str,
        external_thread_id: str = "",
        source_url: str = "",
    ) -> AmoChatMirrorRecord:
        ch = resolve_channel_key(channel)
        existing = self.store.get_by_owner_request(owner_request_id)
        if existing:
            return existing
        if external_thread_id:
            by_thread = self.store.get_by_external_thread(
                channel=ch, external_thread_id=external_thread_id
            )
            if by_thread:
                return by_thread
        conv = stable_conversation_id(
            channel=ch,
            object_id=object_id,
            external_thread_id=external_thread_id,
            owner_request_id=owner_request_id,
            source_url=source_url,
        )
        try:
            cfg = self.config.channel(ch)
            scope = cfg.scope_id
        except Exception:
            scope = ""
        rec = AmoChatMirrorRecord(
            channel=ch,
            owner_request_id=owner_request_id,
            conversation_id=conv,
            external_thread_id=external_thread_id,
            object_id=object_id,
            source_url=source_url,
            scope_id=scope,
        )
        return self.store.upsert(rec)

    def _channel_or_degrade(self, channel: str) -> tuple[Any, MirrorResult | None]:
        ch = resolve_channel_key(channel)
        try:
            cfg = self.config.channel(ch)
        except KeyError:
            return None, MirrorResult(
                ok=False, skipped=True, reason="unknown_channel", degraded=False
            )
        if not cfg.configured or not cfg.connected:
            print(
                f"[agent7.amo_chat] AMO_CHAT_MIRROR_DEGRADED channel={ch} "
                f"reason=not_configured_or_not_connected"
            )
            return cfg, MirrorResult(
                ok=False,
                degraded=True,
                skipped=True,
                reason="AMO_CHAT_MIRROR_DEGRADED",
            )
        return cfg, None

    def mirror_source_message(
        self,
        *,
        channel: str,
        owner_request_id: str,
        object_id: str,
        text: str,
        origin: MessageOrigin,
        external_thread_id: str = "",
        source_url: str = "",
        external_message_id: str = "",
        owner_name: str = "",
    ) -> MirrorResult:
        if not should_mirror_to_amo(origin):
            return MirrorResult(
                ok=True, skipped=True, reason=f"origin_not_mirrored:{origin.value}"
            )

        cfg, early = self._channel_or_degrade(channel)
        if early is not None:
            # Still ensure local mapping exists for later reconnect
            if owner_request_id and object_id:
                self.ensure_record(
                    channel=channel,
                    owner_request_id=owner_request_id,
                    object_id=object_id,
                    external_thread_id=external_thread_id,
                    source_url=source_url,
                )
            return early

        assert cfg is not None
        rec = self.ensure_record(
            channel=channel,
            owner_request_id=owner_request_id,
            object_id=object_id,
            external_thread_id=external_thread_id,
            source_url=source_url,
        )
        direction = (
            "outbound"
            if origin == MessageOrigin.SOURCE_NATIVE_OUTBOUND
            else "inbound"
        )
        msgid = stable_message_id(
            channel=rec.channel,
            direction=direction,
            owner_request_id=owner_request_id,
            external_message_id=external_message_id,
            text_fingerprint=text_fingerprint(text),
        )
        if self.store.already_imported(rec.conversation_id, msgid):
            return MirrorResult(
                ok=True,
                skipped=True,
                reason="duplicate_msgid",
                conversation_id=rec.conversation_id,
                msgid=msgid,
            )

        silent = self.config.owner_silent_default
        if origin == MessageOrigin.SOURCE_NATIVE_OUTBOUND:
            payload = build_outbound_bot_payload(
                conversation_id=rec.conversation_id,
                msgid=msgid,
                text=text,
                channel=rec.channel,
                bot_id=cfg.bot_id,
                silent=silent,
                owner_name=owner_name,
            )
        else:
            payload = build_inbound_owner_payload(
                conversation_id=rec.conversation_id,
                msgid=msgid,
                text=text,
                channel=rec.channel,
                silent=silent,
                owner_name=owner_name,
            )

        resp: AmojoResponse = self.client.import_message(cfg, payload)
        if not resp.ok and not self.dry_run:
            self.store.mark_degraded(rec.conversation_id)
            print(
                f"[agent7.amo_chat] AMO_CHAT_MIRROR_DEGRADED channel={rec.channel} "
                f"error={resp.error or resp.status_code}"
            )
            return MirrorResult(
                ok=False,
                degraded=True,
                reason=resp.error or "import_failed",
                conversation_id=rec.conversation_id,
                msgid=msgid,
            )

        self.store.mark_imported(
            rec.conversation_id,
            msgid,
            direction=direction,
            external_message_id=external_message_id,
            sync_state="READY",
        )
        return MirrorResult(
            ok=True,
            conversation_id=rec.conversation_id,
            msgid=msgid,
            notes=["dry_run"] if self.dry_run or resp.data.get("dry_run") else [],
        )
