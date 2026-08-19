"""Env/config for Open Home amoCRM custom chat channels (Facebook; Airbnb optional)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def airbnb_chat_enabled() -> bool:
    """Airbnb amo custom chat mirror — off by default (Facebook-only production)."""
    return _env_bool("AMO_CHAT_AIRBNB_ENABLED", False)


def _disabled_airbnb_channel(account_id: str) -> AmoChatChannelConfig:
    return AmoChatChannelConfig(
        key="airbnb",
        title="(disabled)",
        channel_id="",
        channel_secret="",
        scope_id="",
        bot_id="",
        account_id=account_id,
        webhook_enabled=False,
    )


def default_state_path() -> Path:
    override = _env("AMO_CHAT_STATE_PATH")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "data" / "amo_chat_state.json"


@dataclass(frozen=True)
class AmoChatChannelConfig:
    key: str  # facebook | airbnb
    title: str
    channel_id: str
    channel_secret: str
    scope_id: str
    bot_id: str
    account_id: str  # amojo account UUID
    webhook_enabled: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.channel_id and self.channel_secret)

    @property
    def connected(self) -> bool:
        return bool(self.scope_id)

    @property
    def ready(self) -> bool:
        return self.configured and self.connected and bool(self.bot_id)

    def redacted(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "channel_id": "SET" if self.channel_id else "MISSING",
            "channel_secret": "SET" if self.channel_secret else "MISSING",
            "scope_id": "SET" if self.scope_id else "MISSING",
            "bot_id": "SET" if self.bot_id else "MISSING",
            "account_id": "SET" if self.account_id else "MISSING",
            "configured": self.configured,
            "connected": self.connected,
            "ready": self.ready,
            "webhook_enabled": self.webhook_enabled,
        }


@dataclass(frozen=True)
class AmoChatConfig:
    amojo_base_url: str
    account_id: str
    owner_silent_default: bool
    webhook_enabled: bool
    airbnb_enabled: bool
    facebook: AmoChatChannelConfig
    airbnb: AmoChatChannelConfig
    state_path: Path

    def channel(self, key: str) -> AmoChatChannelConfig:
        k = (key or "").strip().lower()
        if k in {"facebook", "facebook_messenger", "fb", "fb_marketplace"}:
            return self.facebook
        if k in {"airbnb", "airbnb_messages"}:
            if not self.airbnb_enabled:
                raise KeyError(
                    "amo chat airbnb channel disabled (AMO_CHAT_AIRBNB_ENABLED=false)"
                )
            return self.airbnb
        raise KeyError(f"unknown amo chat channel key: {key}")

    def redacted(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "amojo_base_url": self.amojo_base_url,
            "account_id": "SET" if self.account_id else "MISSING",
            "owner_silent_default": self.owner_silent_default,
            "webhook_enabled": self.webhook_enabled,
            "airbnb_enabled": self.airbnb_enabled,
            "facebook": self.facebook.redacted(),
        }
        if self.airbnb_enabled:
            out["airbnb"] = self.airbnb.redacted()
        else:
            out["airbnb"] = {"key": "airbnb", "disabled": True}
        return out


def load_amo_chat_config() -> AmoChatConfig:
    state = _read_state(default_state_path())
    account_id = _env("AMO_CHAT_ACCOUNT_ID") or str(state.get("account_id") or "")
    fb_scope = _env("AMO_CHAT_FB_SCOPE_ID") or str(
        (state.get("facebook") or {}).get("scope_id") or ""
    )
    airbnb_on = airbnb_chat_enabled()
    ab_scope = ""
    if airbnb_on:
        ab_scope = _env("AMO_CHAT_AIRBNB_SCOPE_ID") or str(
            (state.get("airbnb") or {}).get("scope_id") or ""
        )
    webhook = _env_bool("AMO_CHAT_WEBHOOK_ENABLED", False)
    fb_ch = AmoChatChannelConfig(
        key="facebook",
        title=_env("AMO_CHAT_FB_TITLE", "Open Home | Facebook Marketplace"),
        channel_id=_env("AMO_CHAT_FB_CHANNEL_ID"),
        channel_secret=_env("AMO_CHAT_FB_CHANNEL_SECRET"),
        scope_id=fb_scope,
        bot_id=_env("AMO_CHAT_FB_BOT_ID")
        or str((state.get("facebook") or {}).get("bot_id") or ""),
        account_id=account_id,
        webhook_enabled=webhook,
    )
    ab_ch = (
        AmoChatChannelConfig(
            key="airbnb",
            title=_env("AMO_CHAT_AIRBNB_TITLE", "Open Home | Airbnb"),
            channel_id=_env("AMO_CHAT_AIRBNB_CHANNEL_ID"),
            channel_secret=_env("AMO_CHAT_AIRBNB_CHANNEL_SECRET"),
            scope_id=ab_scope,
            bot_id=_env("AMO_CHAT_AIRBNB_BOT_ID")
            or str((state.get("airbnb") or {}).get("bot_id") or ""),
            account_id=account_id,
            webhook_enabled=webhook,
        )
        if airbnb_on
        else _disabled_airbnb_channel(account_id)
    )
    return AmoChatConfig(
        amojo_base_url=_env("AMO_CHAT_AMOJO_BASE_URL", "https://amojo.amocrm.ru").rstrip(
            "/"
        ),
        account_id=account_id,
        owner_silent_default=_env_bool("AMO_CHAT_OWNER_SILENT", True),
        webhook_enabled=webhook,
        airbnb_enabled=airbnb_on,
        facebook=fb_ch,
        airbnb=ab_ch,
        state_path=default_state_path(),
    )


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def default_env_path() -> Path:
    override = _env("OPENHOME_ENV_FILE")
    if override:
        return Path(override).expanduser()
    return Path("/opt/openhome/.env")


def update_env_file(path: Path, updates: dict[str, str]) -> Path:
    """Merge key=value lines into an env file (never prints values)."""
    target = path.expanduser()
    lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    index: dict[str, int] = {}
    for i, line in enumerate(lines):
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key = text.split("=", 1)[0].strip()
        if key:
            index[key] = i
    for key, value in updates.items():
        line = f"{key}={value}"
        if key in index:
            lines[index[key]] = line
        else:
            lines.append(line)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".env.tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def persist_scope_id_to_env(channel_key: str, scope_id: str, path: Path | None = None) -> Path | None:
    """Write scope_id to .env for facebook/airbnb (no secrets)."""
    scope = (scope_id or "").strip()
    if not scope:
        return None
    key = "facebook" if "face" in channel_key or channel_key == "fb" else (
        "airbnb" if "airbnb" in channel_key else channel_key
    )
    env_key = (
        "AMO_CHAT_FB_SCOPE_ID"
        if key == "facebook"
        else "AMO_CHAT_AIRBNB_SCOPE_ID"
        if key == "airbnb"
        else ""
    )
    if not env_key:
        return None
    if key == "airbnb" and not airbnb_chat_enabled():
        return None
    return update_env_file(path or default_env_path(), {env_key: scope})


def save_channel_scope(
    *,
    channel_key: str,
    scope_id: str,
    account_id: str = "",
    bot_id: str = "",
    path: Path | None = None,
    env_path: Path | None = None,
) -> Path:
    """Persist scope_id locally (no secrets). Idempotent merge."""
    target = path or default_state_path()
    data = _read_state(target)
    if account_id:
        data["account_id"] = account_id
    if "airbnb" in channel_key and not airbnb_chat_enabled():
        raise ValueError("AMO_CHAT_AIRBNB_ENABLED=false — cannot save airbnb scope")
    key = "facebook" if "face" in channel_key or channel_key == "fb" else (
        "airbnb" if "airbnb" in channel_key else channel_key
    )
    entry = dict(data.get(key) or {}) if isinstance(data.get(key), dict) else {}
    if scope_id:
        entry["scope_id"] = scope_id
    if bot_id:
        entry["bot_id"] = bot_id
    data[key] = entry
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    if scope_id:
        persist_scope_id_to_env(channel_key, scope_id, path=env_path)
    return target
