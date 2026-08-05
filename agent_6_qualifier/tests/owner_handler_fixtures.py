"""Shared fixtures for owner message handler tests."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from agent6_qualifier.models import Listing
from agent6_qualifier.qualifier import Session


class FakeStore:
    def __init__(self, session: Session | None = None):
        self.session = session
        self.saved: list[Session] = []

    def find_awaiting_owner(self, tg_username: str) -> Session | None:
        if self.session is None:
            return None
        want = tg_username.strip().lstrip("@").lower()
        chosen = self.session.chosen
        if (
            self.session.awaiting_owner
            and chosen is not None
            and chosen.owner_telegram.strip().lstrip("@").lower() == want
        ):
            return self.session
        if self.session.awaiting_owner and not chosen.owner_telegram:
            return self.session
        return self.session if self.session.awaiting_owner else None

    def find_awaiting_owner_by_object(self, object_id: str) -> Session | None:
        if self.session is None:
            return None
        if (
            self.session.awaiting_owner
            and self.session.chosen is not None
            and self.session.chosen.object_id == object_id
        ):
            return self.session
        return None

    def save(self, session: Session) -> None:
        self.saved.append(session)
        self.session = session


def make_owner_session(
    *,
    chat_id: str = "5041767749",
    object_id: str = "A_20260713_003",
    page_id: str = "page-1",
    amo_lead_id: int | None = 77,
    owner_telegram: str = "@owner_user",
) -> Session:
    session = Session(chat_id=chat_id, amo_lead_id=amo_lead_id)
    session.awaiting_owner = True
    session.lead.preferred_object_id = object_id
    session.lead.check_in = date(2026, 9, 1)
    session.lead.check_out = date(2026, 10, 1)
    session.lead.name = "Ivan"
    session.chosen = Listing(
        object_id=object_id,
        page_id=page_id,
        title="Villa",
        owner_telegram=owner_telegram,
    )
    return session


def make_event(*, chat_id: int = 999888777, text: str = "Да, свободно") -> MagicMock:
    event = MagicMock()
    event.chat_id = chat_id
    event.raw_text = text
    return event


def make_sender(*, username: str = "owner_user") -> MagicMock:
    sender = MagicMock()
    sender.username = username
    return sender
