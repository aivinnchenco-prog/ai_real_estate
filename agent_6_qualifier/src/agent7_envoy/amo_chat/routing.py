"""Resolve amo chat webhook path → channel key (facebook|airbnb)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from agent7_envoy.amo_chat.config import AmoChatConfig, load_amo_chat_config


@dataclass(frozen=True)
class WebhookRouteResult:
    ok: bool
    channel_key: str = ""
    scope_id: str = ""
    reason: str = ""


def normalize_webhook_path(path: str) -> str:
    return (urlparse(path).path or path or "").rstrip("/") or "/"


def resolve_webhook_channel(
    path: str,
    config: AmoChatConfig | None = None,
) -> WebhookRouteResult:
    """Map POST /webhooks/amo-chat/... to facebook|airbnb.

    Accepted forms:
      /webhooks/amo-chat/facebook
      /webhooks/amo-chat/airbnb
      /webhooks/amo-chat/<scope_id>   (must match configured FB/Airbnb scope)

    Unknown scope / unknown suffix → rejected (no silent facebook default).
    Bare /webhooks/amo-chat without scope is rejected once channels use scopes.
    """
    clean = normalize_webhook_path(path)
    if clean in {"/health", "/webhooks/amo-chat/health"}:
        return WebhookRouteResult(ok=True, channel_key="", reason="health")

    if not clean.startswith("/webhooks/amo-chat"):
        return WebhookRouteResult(ok=False, reason="not_found")

    cfg = config or load_amo_chat_config()
    tail = clean[len("/webhooks/amo-chat") :].lstrip("/")
    if not tail:
        return WebhookRouteResult(ok=False, reason="scope_required")

    low = tail.lower()
    if low == "facebook":
        return WebhookRouteResult(ok=True, channel_key="facebook", scope_id=tail)
    if low == "airbnb":
        if not cfg.airbnb_enabled:
            return WebhookRouteResult(ok=False, reason="airbnb_disabled")
        return WebhookRouteResult(ok=True, channel_key="airbnb", scope_id=tail)
    if low == "health":
        return WebhookRouteResult(ok=True, channel_key="", reason="health")

    fb_scope = (cfg.facebook.scope_id or "").strip()
    ab_scope = (cfg.airbnb.scope_id or "").strip()
    if fb_scope and tail == fb_scope:
        return WebhookRouteResult(ok=True, channel_key="facebook", scope_id=tail)
    if ab_scope and tail == ab_scope:
        if not cfg.airbnb_enabled:
            return WebhookRouteResult(ok=False, reason="airbnb_disabled")
        return WebhookRouteResult(ok=True, channel_key="airbnb", scope_id=tail)
    return WebhookRouteResult(ok=False, scope_id=tail, reason="unknown_scope")
