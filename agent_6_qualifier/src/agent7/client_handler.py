"""Shared client message handler for Telegram and WhatsApp channels."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import brain
from .context import build_knowledge, format_history
from .qualifier import Qualifier, Session, Turn


@dataclass
class ClientHandleResult:
    turn: Turn
    reply: str
    silent: bool


def process_client_message(
    session: Session,
    message: str,
    qualifier: Qualifier,
    *,
    polish: bool = True,
) -> ClientHandleResult:
    """Run qualifier pipeline; returns reply text and silence flag."""
    try:
        update = brain.extract_lead_update(
            message,
            session.lead,
            context=build_knowledge(session),
            history=format_history(session.history),
        )
    except Exception:
        update = {}

    turn = qualifier.handle_message(session, message, update)
    if turn.silent:
        return ClientHandleResult(turn=turn, reply="", silent=True)

    if turn.template_key:
        session.last_outbound_template_key = turn.template_key

    reply = turn.reply_draft
    if polish and not turn.skip_polish:
        reply = brain.polish_reply(reply, session.language, session.lead.name)

    return ClientHandleResult(turn=turn, reply=reply, silent=False)


def make_test_qualifier(listings=None) -> Qualifier:
    listings = listings or []
    by_id = {l.object_id: l for l in listings}
    return Qualifier(find_by_id=by_id.get, fetch_all=lambda: listings)
