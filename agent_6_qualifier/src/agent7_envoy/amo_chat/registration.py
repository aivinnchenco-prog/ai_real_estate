"""Safe helpers for amoCRM custom chat channel registration (no secrets)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

# Known Open Home amoCRM integration UUID (verification constant only).
EXPECTED_CLIENT_UUID = "831ec8bc-1565-4c9c-a10d-80b4162dadb6"

FACEBOOK_CHANNEL_CODE = "OpenHomeFacebook"
FACEBOOK_DISPLAY_NAME = "Open Home | Facebook Marketplace"
AIRBNB_CHANNEL_CODE = "OpenHomeAirbnb"
AIRBNB_DISPLAY_NAME = "Open Home | Airbnb"

WEBHOOK_SCOPE_PATH = "/webhooks/amo-chat/:scope_id"

_HTTP_LOCALHOST = re.compile(
    r"^https?://(127\.0\.0\.1|localhost)(:\d+)?/?$", re.IGNORECASE
)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@dataclass(frozen=True)
class AmoAccountInfo:
    account_id: str
    amojo_id: str
    subdomain: str

    @property
    def ready(self) -> bool:
        return bool(self.account_id and self.amojo_id and self.subdomain)


@dataclass(frozen=True)
class ClientUuidCheck:
    current: str
    expected: str
    match: bool
    source: str  # env | expected_default | mismatch


@dataclass(frozen=True)
class WebhookUrlBuildResult:
    ok: bool
    url: str
    reason: str = ""


def parse_account_payload(payload: dict[str, Any] | None) -> AmoAccountInfo:
    """Extract safe account fields from GET /api/v4/account?with=amojo_id."""
    data = payload if isinstance(payload, dict) else {}
    account_id = data.get("id")
    amojo_id = data.get("amojo_id")
    if not amojo_id and isinstance(data.get("_embedded"), dict):
        amojo_id = data["_embedded"].get("amojo_id")
    subdomain = data.get("subdomain") or _env("AMO_SUBDOMAIN")
    if isinstance(subdomain, str):
        subdomain = subdomain.replace("https://", "").split(".")[0].strip()
    else:
        subdomain = ""
    return AmoAccountInfo(
        account_id=str(account_id).strip() if account_id is not None else "",
        amojo_id=str(amojo_id).strip() if amojo_id else "",
        subdomain=subdomain,
    )


def require_amojo_id(info: AmoAccountInfo) -> None:
    if not info.amojo_id:
        raise ValueError("missing amojo_id in account payload")


def resolve_client_uuid(
    *,
    configured: str | None = None,
    expected: str = EXPECTED_CLIENT_UUID,
) -> ClientUuidCheck:
    """Compare configured integration UUID to the expected Open Home UUID.

    Does not invent a new UUID. If env/config is empty, reports the expected
    value as the registration target (source=expected_default) with match=True
    only when no conflicting UUID is present.
    """
    current = (configured if configured is not None else _env("AMO_CHAT_CLIENT_UUID")).strip()
    exp = (expected or EXPECTED_CLIENT_UUID).strip()
    if not current:
        return ClientUuidCheck(
            current=exp,
            expected=exp,
            match=True,
            source="expected_default",
        )
    matched = current.lower() == exp.lower()
    return ClientUuidCheck(
        current=current,
        expected=exp,
        match=matched,
        source="env" if matched else "mismatch",
    )


def normalize_public_base_url(raw: str) -> str:
    return (raw or "").strip().rstrip("/")


def build_registration_webhook_url(
    public_base_url: str,
    *,
    require_https: bool = True,
    scope_placeholder: str = ":scope_id",
) -> WebhookUrlBuildResult:
    """Build `${BASE}/webhooks/amo-chat/:scope_id` for channel registration.

    Rejects empty, non-http(s), bare HTTP for production registration, and
    localhost placeholders. Preserves path prefix on the base URL.
    """
    base = normalize_public_base_url(public_base_url)
    if not base:
        return WebhookUrlBuildResult(ok=False, url="", reason="not_configured")

    parsed = urlparse(base)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return WebhookUrlBuildResult(ok=False, url="", reason="invalid_public_url")
    if not parsed.netloc:
        return WebhookUrlBuildResult(ok=False, url="", reason="invalid_public_url")
    if _HTTP_LOCALHOST.match(base):
        return WebhookUrlBuildResult(ok=False, url="", reason="localhost_rejected")
    if require_https and scheme != "https":
        return WebhookUrlBuildResult(ok=False, url="", reason="http_rejected")

    path_prefix = (parsed.path or "").rstrip("/")
    url = f"{parsed.scheme}://{parsed.netloc}{path_prefix}/webhooks/amo-chat/{scope_placeholder}"
    return WebhookUrlBuildResult(ok=True, url=url, reason="")


def public_base_url_from_env() -> str:
    return normalize_public_base_url(_env("AMO_CHAT_PUBLIC_BASE_URL"))


def local_webhook_base_url() -> str:
    host = _env("AMO_CHAT_WEBHOOK_HOST", "127.0.0.1") or "127.0.0.1"
    port = _env("AMO_CHAT_WEBHOOK_PORT", "8766") or "8766"
    return f"http://{host}:{port}"


def channel_registration_labels() -> dict[str, dict[str, str]]:
    from agent7_envoy.amo_chat.config import airbnb_chat_enabled

    labels = {
        "facebook": {
            "code": FACEBOOK_CHANNEL_CODE,
            "display_name": FACEBOOK_DISPLAY_NAME,
        },
    }
    if airbnb_chat_enabled():
        labels["airbnb"] = {
            "code": AIRBNB_CHANNEL_CODE,
            "display_name": AIRBNB_DISPLAY_NAME,
        }
    return labels
