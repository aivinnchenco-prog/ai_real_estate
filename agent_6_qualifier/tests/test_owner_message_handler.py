"""Regression tests for agent6_qualifier.tg_userbot.handle_owner_message runtime contract."""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.models import Availability
from agent6_qualifier.qualifier import Session
from agent6_qualifier.tg_userbot import OWNERS_FOLDER, handle_owner_message
from agent7_envoy.owner_result import OwnerVerdict
from owner_handler_fixtures import FakeStore, make_event, make_owner_session, make_sender


@pytest.fixture
def handler_env(monkeypatch):
    """Isolate tg_userbot globals and external side effects."""
    store = FakeStore()
    monkeypatch.setattr("agent6_qualifier.tg_userbot._store", store)
    monkeypatch.setattr("agent6_qualifier.tg_userbot._sessions", {})
    monkeypatch.setattr(
        "agent6_qualifier.tg_userbot.brain.polish_reply",
        lambda draft, language, name: draft,
    )
    monkeypatch.setattr(
        "agent6_qualifier.tg_userbot.humanized_respond",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "agent6_qualifier.tg_userbot.add_to_folder",
        AsyncMock(),
    )
    notion_updates: list[dict] = []
    notify_errors: list[tuple] = []

    def _update_availability(page_id, status, *, busy_until=None, future_bookings=""):
        notion_updates.append({
            "page_id": page_id,
            "status": status,
            "busy_until": busy_until,
            "future_bookings": future_bookings,
        })

    monkeypatch.setattr(
        "agent6_qualifier.tg_userbot.notion_store.update_availability",
        _update_availability,
    )
    monkeypatch.setattr(
        "agent6_qualifier.tg_userbot.notify_error",
        lambda component, error, context="": notify_errors.append((component, error, context)),
    )

    mark_owner = MagicMock()
    monkeypatch.setattr("agent7_envoy.owner_registry.mark_owner", mark_owner)
    get_owner = MagicMock(return_value={"object_id": "A_20260713_003"})
    monkeypatch.setattr("agent7_envoy.owner_registry.get_owner", get_owner)

    client = MagicMock()
    client.send_message = AsyncMock()

    amo = MagicMock()
    amo.ensure_pipeline.return_value = {"Согласование условий": 555}
    amo.update_lead_status = MagicMock()
    amo.note_owner = MagicMock()
    amo.note_client = MagicMock()

    return {
        "store": store,
        "client": client,
        "amo": amo,
        "notion_updates": notion_updates,
        "notify_errors": notify_errors,
        "mark_owner": mark_owner,
        "get_owner": get_owner,
        "humanized_respond": sys.modules["agent6_qualifier.tg_userbot"].humanized_respond,
        "add_to_folder": sys.modules["agent6_qualifier.tg_userbot"].add_to_folder,
    }


def run_handler(client, event, sender, amo, verdict: OwnerVerdict, monkeypatch):
    monkeypatch.setattr(
        "agent7_envoy.owner_result.parse_owner_reply",
        lambda text, session: verdict,
    )
    return asyncio.run(handle_owner_message(client, event, sender, amo))


def test_owner_free_confirms_availability(handler_env, monkeypatch):
    """Scenario A: owner confirms availability (free verdict)."""
    session = make_owner_session()
    handler_env["store"].session = session
    event = make_event(text="Да, свободно на эти даты")
    sender = make_sender()

    result = run_handler(
        handler_env["client"], event, sender, handler_env["amo"],
        OwnerVerdict(status="free"), monkeypatch,
    )

    assert result is True
    handler_env["mark_owner"].assert_called_once()
    handler_env["humanized_respond"].assert_awaited_once()
    ack_text = handler_env["humanized_respond"].await_args.args[1]
    from agent6_qualifier.templates import OWNER_ACK_FREE
    assert ack_text == OWNER_ACK_FREE

    handler_env["client"].send_message.assert_awaited_once()
    client_chat_id, reply = handler_env["client"].send_message.await_args.args
    assert client_chat_id == int(session.chat_id)
    assert "подтвердил" in reply.lower()
    assert session.chat_id == "5041767749"

    assert session.awaiting_owner is False
    assert session.owner_verdict == "free"
    assert session.chosen.availability == Availability.FREE
    assert session.booking_confirmed is False

    assert len(handler_env["notion_updates"]) == 1
    assert handler_env["notion_updates"][0]["status"] == Availability.FREE

    assert len(handler_env["store"].saved) == 1
    assert handler_env["store"].saved[0] is session
    from agent6_qualifier import tg_userbot
    assert tg_userbot._sessions[session.chat_id] is session

    handler_env["add_to_folder"].assert_awaited_once_with(
        handler_env["client"], sender, OWNERS_FOLDER,
    )
    handler_env["amo"].ensure_pipeline.assert_called_once()
    handler_env["amo"].update_lead_status.assert_called_once_with(77, 555)
    handler_env["amo"].note_owner.assert_called_once()
    handler_env["amo"].note_client.assert_called_once()


def test_owner_busy_notifies_client(handler_env, monkeypatch):
    """Scenario B: busy verdict with busy_until reaches client."""
    session = make_owner_session()
    handler_env["store"].session = session
    verdict = OwnerVerdict(
        status="busy",
        busy_until=date(2026, 9, 15),
        future_bookings="20–25 сентября",
    )

    result = run_handler(
        handler_env["client"], make_event(text="Занято до 15.09"),
        make_sender(), handler_env["amo"], verdict, monkeypatch,
    )

    assert result is True
    from agent6_qualifier.templates import OWNER_ACK_CONDITIONS
    assert handler_env["humanized_respond"].await_args.args[1] == OWNER_ACK_CONDITIONS

    reply = handler_env["client"].send_message.await_args.args[1]
    assert "занят" in reply.lower()
    assert session.owner_verdict == "busy"
    assert session.chosen.availability == Availability.BUSY
    assert session.chosen.busy_until == date(2026, 9, 15)
    assert session.booking_confirmed is False

    assert handler_env["notion_updates"][0]["status"] == Availability.BUSY
    assert handler_env["notion_updates"][0]["busy_until"] == date(2026, 9, 15)
    assert len(handler_env["store"].saved) == 1
    handler_env["amo"].update_lead_status.assert_called_once()


def test_owner_busy_without_dates_asks_followup_only(handler_env, monkeypatch):
    """Scenario C: busy without busy_until — owner follow-up, no client side effects."""
    session = make_owner_session()
    handler_env["store"].session = session
    verdict = OwnerVerdict(status="busy")

    result = run_handler(
        handler_env["client"], make_event(text="Занято"),
        make_sender(), handler_env["amo"], verdict, monkeypatch,
    )

    assert result is True
    from agent6_qualifier.templates import OWNER_BUSY_FOLLOWUP
    handler_env["humanized_respond"].assert_awaited_once_with(
        handler_env["humanized_respond"].await_args.args[0],
        OWNER_BUSY_FOLLOWUP,
    )
    handler_env["client"].send_message.assert_not_awaited()
    assert session.awaiting_owner is True
    assert session.owner_verdict == ""
    assert handler_env["notion_updates"] == []
    assert handler_env["store"].saved == []
    handler_env["amo"].ensure_pipeline.assert_not_called()
    handler_env["add_to_folder"].assert_not_awaited()


def test_unknown_sender_not_in_registry_returns_false(handler_env, monkeypatch):
    """Scenario D: no registry entry and no awaiting session."""
    handler_env["get_owner"].return_value = None
    handler_env["store"].session = None

    result = run_handler(
        handler_env["client"], make_event(), make_sender(username="stranger"),
        handler_env["amo"], OwnerVerdict(status="free"), monkeypatch,
    )

    assert result is False
    handler_env["humanized_respond"].assert_not_awaited()
    handler_env["client"].send_message.assert_not_awaited()
    handler_env["mark_owner"].assert_not_called()
    handler_env["amo"].ensure_pipeline.assert_not_called()


def test_registered_owner_without_active_session_is_swallowed(handler_env, monkeypatch):
    """Scenario E: registry hit but no awaiting session — log-only exit."""
    handler_env["get_owner"].return_value = {"object_id": "A_20260713_003"}
    handler_env["store"].session = None

    result = run_handler(
        handler_env["client"], make_event(text="Привет"),
        make_sender(), handler_env["amo"], OwnerVerdict(status="free"), monkeypatch,
    )

    assert result is True
    handler_env["humanized_respond"].assert_not_awaited()
    handler_env["client"].send_message.assert_not_awaited()
    handler_env["mark_owner"].assert_not_called()
    assert handler_env["notion_updates"] == []
    assert handler_env["store"].saved == []
    from agent7 import tg_userbot
    assert tg_userbot._sessions == {}


def test_notion_error_does_not_block_client_message(handler_env, monkeypatch):
    """Scenario F (Notion): error is logged; verdict and client message still apply."""
    session = make_owner_session()
    handler_env["store"].session = session

    def _boom(*args, **kwargs):
        raise RuntimeError("notion down")

    monkeypatch.setattr("agent6_qualifier.tg_userbot.notion_store.update_availability", _boom)

    result = run_handler(
        handler_env["client"], make_event(), make_sender(),
        handler_env["amo"], OwnerVerdict(status="free"), monkeypatch,
    )

    assert result is True
    assert any(e[0] == "notion.availability" for e in handler_env["notify_errors"])
    handler_env["client"].send_message.assert_awaited_once()
    assert session.owner_verdict == "free"
    assert len(handler_env["store"].saved) == 1


def test_amo_error_after_client_message(handler_env, monkeypatch):
    """Scenario F (amoCRM): error is logged; client message and session save remain."""
    session = make_owner_session()
    handler_env["store"].session = session
    handler_env["amo"].ensure_pipeline.side_effect = RuntimeError("amo down")

    result = run_handler(
        handler_env["client"], make_event(), make_sender(),
        handler_env["amo"], OwnerVerdict(status="free"), monkeypatch,
    )

    assert result is True
    assert any(e[0] == "amo.owner_flow" for e in handler_env["notify_errors"])
    handler_env["client"].send_message.assert_awaited_once()
    assert session.owner_verdict == "free"
    assert len(handler_env["store"].saved) == 1
    assert session.history[-1]["role"] == "assistant"


def test_session_found_via_registry_object_id(handler_env, monkeypatch):
    """Registry object_id fallback when username lookup misses."""
    session = make_owner_session(owner_telegram="")
    handler_env["store"].session = session
    handler_env["get_owner"].return_value = {"object_id": session.chosen.object_id}

    store = handler_env["store"]

    def _find_user(_username: str):
        return None

    monkeypatch.setattr(store, "find_awaiting_owner", _find_user)

    result = run_handler(
        handler_env["client"], make_event(), make_sender(),
        handler_env["amo"], OwnerVerdict(status="free"), monkeypatch,
    )

    assert result is True
    handler_env["client"].send_message.assert_awaited_once()
