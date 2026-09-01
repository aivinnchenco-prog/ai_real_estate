import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.handoff_control import (
    apply_responsible_user_change,
    handoff_manual_only,
)
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    apply_inbound_to_ownership,
)
from agent6_qualifier.messaging.types import CanonicalInboundMessage, ConversationOwner
from agent6_qualifier.qualifier import Qualifier, Session
from agent6_qualifier.session_ownership import should_bot_respond
from agent6_qualifier.sessions import SessionStore


def _human_outbound(text="менеджер: сейчас посмотрю") -> CanonicalInboundMessage:
    return CanonicalInboundMessage(
        provider="wazzup",
        channel="whatsapp",
        message_id="h1",
        chat_id="wa-test",
        phone="66999990000",
        direction="outbound",
        text=text,
        timestamp=datetime.now(timezone.utc),
        crm_message_id="amo-123",
        is_from_bot=False,
    )


def test_manual_only_default_on():
    assert handoff_manual_only() is True


def test_human_outbound_stops_next_client_turn():
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    session = Session(chat_id="wa-test")
    state = ConversationOwnershipState(chat_id="wa-test")
    apply_inbound_to_ownership(state, _human_outbound())
    assert state.manager_takeover is True
    assert state.bot_may_reply() is False

    from agent6_qualifier.handoff_control import activate_stop

    activate_stop(session, state, reason="human_outbound", lead_id=99, pipeline_id=1)
    turn = q.handle_message(session, "есть варианты?", {})
    assert turn.silent


def test_ttl_48h_still_silent_in_manual_only():
    session = Session(chat_id="old")
    old = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
    session.human_handoff_active = True
    session.human_handoff_at = old
    session.last_human_message_at = old
    state = ConversationOwnershipState(
        chat_id="old",
        owner=ConversationOwner.HUMAN_HANDOFF,
        manager_takeover=True,
        last_human_activity_at=old,
    )
    assert should_bot_respond(session) is False
    assert state.bot_may_reply() is False
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    assert q.handle_message(session, "привет", {}).silent


def test_client_resume_phrase_ignored():
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    session = Session(chat_id="r")
    session.human_handoff_active = True
    session.human_handoff_at = datetime.now(timezone.utc).isoformat()
    turn = q.handle_message(session, "продолжу сам, без менеджера", {})
    assert turn.silent
    assert session.human_handoff_active is True


def test_return_responsible_to_ai_resumes(monkeypatch):
    monkeypatch.setenv("AMO_DEFAULT_RESPONSIBLE_USER_ID", "100")
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    session = Session(chat_id="ai", amo_lead_id=77, amo_responsible_user_id=100)
    state = ConversationOwnershipState(chat_id="ai")
    apply_responsible_user_change(
        new_user_id=200, session=session, ownership=state,
        lead_id=77, pipeline_id=5, previous_user_id=100,
    )
    assert session.human_handoff_active is True
    assert state.bot_may_reply() is False
    assert q.handle_message(session, "есть варианты?", {}).silent

    apply_responsible_user_change(
        new_user_id=100, session=session, ownership=state,
        lead_id=77, pipeline_id=5, previous_user_id=200,
    )
    assert session.human_handoff_active is False
    assert state.bot_may_reply() is True
    turn = q.handle_message(session, "здравствуйте", {})
    assert not turn.silent


def test_handoff_survives_session_reload(tmp_path):
    store = SessionStore(tmp_path)
    session = Session(chat_id="persist1", amo_lead_id=42)
    session.human_handoff_active = True
    session.handoff_to_human = True
    session.human_handoff_at = datetime.now(timezone.utc).isoformat()
    store.save(session)
    loaded = store.load("persist1")
    assert loaded is not None
    assert loaded.human_handoff_active is True
    assert should_bot_respond(loaded) is False
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    assert q.handle_message(loaded, "нужны варианты", {}).silent
