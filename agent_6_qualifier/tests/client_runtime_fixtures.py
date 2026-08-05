"""Shared fixtures for client runtime orchestration tests."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

from agent6_qualifier.qualifier import Session, Turn


class FakeStore:
    def __init__(self):
        self.saved: list[Session] = []
        self._by_chat: dict[str, Session] = {}

    def load(self, chat_id: str) -> Session | None:
        return self._by_chat.get(chat_id)

    def save(self, session: Session) -> None:
        self.saved.append(session)
        self._by_chat[session.chat_id] = session


def make_client_session(
    *,
    chat_id: str = "5041767749",
    amo_lead_id: int | None = 77,
    preferred_object_id: str = "A_20260713_003",
    owner_verdict: str = "",
    handoff_to_human: bool = False,
) -> Session:
    session = Session(chat_id=chat_id, amo_lead_id=amo_lead_id)
    session.lead.preferred_object_id = preferred_object_id
    session.lead.full_name = "Ivan Ivanov"
    session.lead.citizenship = "RU"
    session.lead.whatsapp = "+79990001122"
    session.lead.guests = 2
    session.owner_verdict = owner_verdict
    session.handoff_to_human = handoff_to_human
    return session


def make_turn(**kwargs) -> Turn:
    defaults = {
        "reply_draft": "Тестовый ответ клиенту",
        "events": [],
        "need_owner_check": False,
        "booking_confirmed": False,
        "handoff_to_human": False,
        "skip_polish": True,
    }
    defaults.update(kwargs)
    return Turn(**defaults)


def make_event(
    *,
    chat_id: int = 5041767749,
    text: str = "Привет",
    message_id: int = 42,
    is_private: bool = True,
) -> MagicMock:
    event = MagicMock()
    event.is_private = is_private
    event.chat_id = chat_id
    event.raw_text = text
    event.message.id = message_id
    event.get_sender = AsyncMock()
    return event


def make_sender(*, username: str = "client_user", is_bot: bool = False) -> MagicMock:
    sender = MagicMock()
    sender.username = username
    sender.bot = is_bot
    sender.first_name = "Client"
    sender.last_name = "User"
    sender.phone = ""
    return sender


@dataclass
class RuntimeHarness:
    on_message: object
    client: MagicMock
    amo: MagicMock
    store: FakeStore
    qualifier: MagicMock
    main_task: object
    outreach_tasks: list = field(default_factory=list)
    manager_calls: list = field(default_factory=list)
    notary_calls: list = field(default_factory=list)
    outreach_calls: list = field(default_factory=list)
    notify_errors: list = field(default_factory=list)
    events: list = field(default_factory=list)

    async def run(self, event, sender) -> None:
        await self.on_message(event)

    async def drain_outreach_tasks(self, *, absorb_errors: bool = False) -> None:
        for task in list(self.outreach_tasks):
            try:
                await task
            except Exception:
                if not absorb_errors:
                    raise

    def assert_outreach_inflight_clear(self, chat_id: str) -> None:
        import agent6_qualifier.tg_userbot as tg
        assert chat_id not in tg._outreach_inflight

    async def shutdown(self) -> None:
        for task in list(self.outreach_tasks):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.main_task.cancel()
        try:
            await self.main_task
        except asyncio.CancelledError:
            pass
