"""amoCRM manager reply → Facebook Messenger (Agent7 transport)."""

from __future__ import annotations

import os
from typing import Any, Callable

from agent7_envoy.messaging.base import SendTextRequest
from agent7_envoy.messaging.facebook_messenger import FacebookMessengerOwnerTransport

SendToSourceFn = Callable[..., Any]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def manager_reply_live() -> bool:
    """Live amo→FB sends from webhook handler (off until controlled smoke)."""
    return _env_bool("AMO_CHAT_MANAGER_REPLY_LIVE", False)


def facebook_messenger_thread_url(thread_id: str) -> str:
    tid = (thread_id or "").strip()
    if not tid:
        return ""
    if tid.startswith("http://") or tid.startswith("https://"):
        return tid
    return f"https://www.facebook.com/messages/t/{tid}"


def build_facebook_send_to_source(
    transport: FacebookMessengerOwnerTransport | None = None,
    *,
    dry_run: bool | None = None,
) -> SendToSourceFn:
    """Return webhook send_to_source callback using Agent7 FB profile."""

    fb = transport or FacebookMessengerOwnerTransport()

    def send_to_source(**kw: Any) -> Any:
        channel = str(kw.get("channel") or "").strip().lower()
        if channel not in {"facebook", "facebook_messenger", "fb", "fb_marketplace"}:
            return _result("UNSUPPORTED_CHANNEL")

        text = str(kw.get("text") or "").strip()
        thread = str(kw.get("external_thread_id") or "").strip()
        source_url = str(kw.get("source_url") or "").strip()
        url = source_url if "facebook" in source_url.lower() else facebook_messenger_thread_url(thread)
        if not url:
            return _result("THREAD_UNAVAILABLE")

        use_dry = dry_run if dry_run is not None else not manager_reply_live()
        req = SendTextRequest(
            text=text,
            destination=url,
            source_url=url,
            owner_request_id=str(kw.get("owner_request_id") or ""),
            object_id=str(kw.get("object_id") or ""),
            expected_thread_id=thread,
            dry_run=use_dry,
        )
        send_result = fb.send_text(req)
        return _result(send_result.outcome.value, send_result=send_result)

    return send_to_source


def _result(outcome: str, send_result: Any = None) -> Any:
    return type(
        "SendToSourceResult",
        (),
        {"outcome": outcome, "send_result": send_result},
    )()
