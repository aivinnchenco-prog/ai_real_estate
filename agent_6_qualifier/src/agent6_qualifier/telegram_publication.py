"""Telegram message → publication resolver integration."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .publication_resolver import PublicationResolution, resolve_publication_reference
from .publication_session import apply_publication_resolution
from .publication_url_normalize import extract_urls_from_text
from .qualifier import Session
from .templates import client_publication_ambiguous, client_publication_unresolved


def extract_telegram_message_metadata(event: Any) -> dict[str, Any]:
    """Best-effort metadata from Telethon message (no network)."""
    meta: dict[str, Any] = {"source": "telegram"}
    message = getattr(event, "message", None)
    if message is None:
        return meta

    fwd = getattr(message, "fwd_from", None)
    if fwd:
        meta["telegram_forward"] = True
        from_id = getattr(fwd, "from_id", None)
        channel = None
        if from_id is not None:
            channel = getattr(from_id, "channel_id", None) or getattr(from_id, "username", None)
        if channel and not isinstance(channel, str):
            channel = getattr(fwd, "post_author", None) or str(channel)
        post_id = getattr(fwd, "channel_post", None) or getattr(message, "fwd_from_id", None)
        if post_id is not None:
            meta["forward_message_id"] = post_id
        if channel:
            meta["forward_channel"] = str(channel).lstrip("@")

    if getattr(message, "reply_to", None):
        replied = message.reply_to
        meta["reply_to_msg_id"] = getattr(replied, "reply_to_msg_id", None)

    return meta


async def handle_publication_reference(
    event: Any,
    session: Session,
    *,
    store: Any,
    find_by_id: Callable[[str], Any],
    respond: Callable[[Any, str], Awaitable[None]],
    notify_diagnostic: Callable[[str], None] | None = None,
) -> bool:
    """Resolve publication before qualifier. Returns True if flow should stop."""
    text = getattr(event, "raw_text", None) or ""
    meta = extract_telegram_message_metadata(event)
    urls = extract_urls_from_text(text)

    resolution: PublicationResolution = resolve_publication_reference(
        text,
        urls,
        "telegram",
        None,
        meta,
        store=store,
    )

    if resolution.confidence == "ambiguous":
        await respond(event, client_publication_ambiguous(resolution.candidates))
        return True

    if resolution.found:
        apply_publication_resolution(session, resolution, find_by_id=find_by_id)
        return False

    if urls and resolution.reason in {"url_not_in_mapping", "no_match", "unresolved_short_url"}:
        if notify_diagnostic:
            notify_diagnostic(
                f"publication unresolved chat={session.chat_id} "
                f"platform={resolution.platform or '-'} "
                f"url={resolution.canonical_url or urls[0][:120]}"
            )
        await respond(event, client_publication_unresolved())
        return True

    return False
