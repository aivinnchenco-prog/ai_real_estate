"""Validate amo chat channel env presence (no secret values printed)."""

from __future__ import annotations

from dataclasses import dataclass

from agent7_envoy.amo_chat.config import AmoChatChannelConfig, AmoChatConfig, load_amo_chat_config


@dataclass(frozen=True)
class ChannelPresence:
    key: str
    channel_id: bool
    channel_secret: bool
    bot_id: bool
    scope_id: bool

    @property
    def credentials_ready(self) -> bool:
        return self.channel_id and self.channel_secret and self.bot_id

    @property
    def connected(self) -> bool:
        return self.credentials_ready and self.scope_id


def channel_presence(ch: AmoChatChannelConfig) -> ChannelPresence:
    return ChannelPresence(
        key=ch.key,
        channel_id=bool(ch.channel_id),
        channel_secret=bool(ch.channel_secret),
        bot_id=bool(ch.bot_id),
        scope_id=bool(ch.scope_id),
    )


def validate_channel_config(cfg: AmoChatConfig | None = None) -> dict[str, ChannelPresence]:
    config = cfg or load_amo_chat_config()
    return {
        "facebook": channel_presence(config.facebook),
        "airbnb": channel_presence(config.airbnb),
    }


def format_channel_presence_report(cfg: AmoChatConfig | None = None) -> str:
    data = validate_channel_config(cfg)
    lines: list[str] = []
    for key in ("facebook", "airbnb"):
        p = data[key]
        lines.append(f"{key.upper()}:")
        lines.append(f"channel_id {'present' if p.channel_id else 'MISSING'}")
        lines.append(f"secret {'present' if p.channel_secret else 'MISSING'}")
        lines.append(f"bot_id {'present' if p.bot_id else 'MISSING'}")
        lines.append(f"scope_id {'present' if p.scope_id else 'MISSING'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
