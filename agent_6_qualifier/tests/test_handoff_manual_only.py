import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.client_handler import process_client_message
from agent6_qualifier.handoff_control import (
    activate_stop,
    apply_amo_tags,
    apply_external_outbound_stop,
    chat_is_paused,
    handoff_manual_only,
    is_greeting_allowlisted,
    resume_bot_manual,
    sync_from_amo_tags,
)
from agent6_qualifier.messaging.ownership import (
    ConversationOwnershipState,
    apply_inbound_to_ownership,
    classify_outbound_source,
)
from agent6_qualifier.messaging.types import CanonicalInboundMessage, ConversationOwner
from agent6_qualifier.qualifier import Qualifier, Session
from agent6_qualifier.session_ownership import should_bot_respond
from agent6_qualifier.sessions import SessionStore
from test_client_handler_service import build_process_env


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


def _agent6_outbound(text="какой район?") -> CanonicalInboundMessage:
    return CanonicalInboundMessage(
        provider="wazzup",
        channel="whatsapp",
        message_id="b1",
        chat_id="wa-test",
        phone="66999990000",
        direction="outbound",
        text=text,
        timestamp=datetime.now(timezone.utc),
        crm_message_id="agent6-abc",
        is_from_bot=True,
    )


def _unknown_outbound(text: str, crm_id=None) -> CanonicalInboundMessage:
    return CanonicalInboundMessage(
        provider="wazzup",
        channel="whatsapp",
        message_id="u1",
        chat_id="wa-test",
        phone="66999990000",
        direction="outbound",
        text=text,
        timestamp=datetime.now(timezone.utc),
        crm_message_id=crm_id,
        is_from_bot=None,
    )


class FakeAmo:
    def __init__(
        self,
        tags: list[str],
        pipeline_id: int = 5,
        *,
        responsible_user_id: int = 100,
    ):
        self.tags = list(tags)
        self.pipeline_id = pipeline_id
        self.responsible_user_id = responsible_user_id
        self.replaced: list[str] | None = None
        self.gets = 0
        self.update_lead_fields = MagicMock()

    def get_lead(self, lead_id, *, with_tags: bool = False):
        self.gets += 1
        return {
            "id": lead_id,
            "pipeline_id": self.pipeline_id,
            "responsible_user_id": self.responsible_user_id,
            "_embedded": {"tags": [{"name": n} for n in self.tags]},
        }

    def lead_tag_names(self, lead_id):
        return list(self.tags)

    def replace_lead_tags(self, lead_id, names):
        self.replaced = list(names)
        self.tags = list(names)


def test_manual_only_default_on():
    assert handoff_manual_only() is True


def test_human_outbound_stops_next_client_turn():
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    session = Session(chat_id="wa-test")
    state = ConversationOwnershipState(chat_id="wa-test")
    apply_inbound_to_ownership(state, _human_outbound())
    assert state.manager_takeover is True
    assert state.bot_may_reply() is False
    assert "C: chat" in state.reason

    activate_stop(session, state, reason="C: chat", lead_id=99, pipeline_id=1)
    turn = q.handle_message(session, "есть варианты?", {})
    assert turn.silent
    assert chat_is_paused(session, state)


def test_agent6_outbound_does_not_pause():
    state = ConversationOwnershipState(chat_id="wa-test")
    apply_inbound_to_ownership(state, _agent6_outbound())
    assert classify_outbound_source(_agent6_outbound()) == "OWN_BOT_OUTBOUND"
    assert state.manager_takeover is False
    assert state.bot_may_reply() is True
    assert state.owner == ConversationOwner.BOT_ACTIVE


def test_unknown_outbound_stops_unless_allowlisted(monkeypatch):
    state = ConversationOwnershipState(chat_id="wa-test")
    greeting = "Hello! Thank you for contacting us. We'll get back to you soon."
    apply_inbound_to_ownership(state, _unknown_outbound(greeting))
    assert state.manager_takeover is True
    assert "UNKNOWN_EXTERNAL_OUTBOUND" in state.reason

    monkeypatch.setenv("AGENT6_GREETING_ALLOWLIST", greeting)
    state2 = ConversationOwnershipState(chat_id="wa-test-2")
    apply_inbound_to_ownership(state2, _unknown_outbound(greeting))
    assert state2.manager_takeover is False
    assert state2.bot_may_reply() is True
    assert is_greeting_allowlisted(greeting)

    # Explicit human outbound is never allowlisted.
    state3 = ConversationOwnershipState(chat_id="wa-test-3")
    apply_inbound_to_ownership(state3, _human_outbound(greeting))
    assert state3.manager_takeover is True


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


def test_no_responsible_user_resume_api():
    import agent6_qualifier.handoff_control as hc

    assert not hasattr(hc, "sync_from_amo_lead")
    assert not hasattr(hc, "apply_responsible_user_change")
    assert not hasattr(hc, "ai_responsible_user_id")


def test_c_holds_across_three_client_turns_when_responsible_is_ai(monkeypatch):
    """The case that variant A broke: lead owner stays AI, C must still hold."""
    monkeypatch.setenv("AMO_DEFAULT_RESPONSIBLE_USER_ID", "100")
    session = Session(
        chat_id="5041767749", amo_lead_id=77, amo_responsible_user_id=100,
    )
    state = ConversationOwnershipState(chat_id=session.chat_id)
    apply_inbound_to_ownership(state, _human_outbound())
    activate_stop(session, state, reason="C: chat", lead_id=77, pipeline_id=5)
    amo = FakeAmo([], responsible_user_id=100)
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])

    for i in range(3):
        kwargs, events, *_rest = build_process_env(session=session)
        kwargs["amo"] = amo
        kwargs["handle_message"] = lambda s, t, u, _q=q: _q.handle_message(s, t, u)
        asyncio.run(process_client_message(**kwargs))
        assert "extract" not in events, f"turn {i + 1} extracted while paused"
        assert "client_reply" not in events, f"turn {i + 1} sent while paused"
        assert "amo_fields" not in events, f"turn {i + 1} wrote amo while paused"
        assert session.human_handoff_active is True
        assert q.handle_message(session, f"ещё раз {i}", {}).silent
        amo.responsible_user_id = 100  # still AI on every GET


def test_responsible_user_change_does_not_affect_pause(monkeypatch):
    monkeypatch.setenv("AMO_DEFAULT_RESPONSIBLE_USER_ID", "100")
    session = Session(
        chat_id="5041767749", amo_lead_id=77, amo_responsible_user_id=100,
    )
    state = ConversationOwnershipState(chat_id=session.chat_id)
    activate_stop(session, state, reason="C: chat", lead_id=77, pipeline_id=5)
    amo = FakeAmo([], responsible_user_id=200)
    kwargs, events, *_rest = build_process_env(session=session)
    kwargs["amo"] = amo
    asyncio.run(process_client_message(**kwargs))
    assert session.human_handoff_active is True
    assert "client_reply" not in events

    amo.responsible_user_id = 100
    session.amo_tags_checked_at = ""
    kwargs, events, *_rest = build_process_env(session=session)
    kwargs["amo"] = amo
    asyncio.run(process_client_message(**kwargs))
    assert session.human_handoff_active is True
    assert "client_reply" not in events


def test_start_then_c_stops_again_hanging_start_ignored():
    session = Session(chat_id="cycle", amo_lead_id=12)
    state = ConversationOwnershipState(chat_id="cycle")
    amo = FakeAmo(["Бот: старт"], responsible_user_id=100)
    assert sync_from_amo_tags(amo, session, state, force=True) == "start"
    assert session.human_handoff_active is False

    apply_external_outbound_stop(state, session=session, reason="C: chat", lead_id=12)
    assert session.human_handoff_active is True

    amo.tags = ["Бот: старт"]  # leftover / hanging
    session.amo_tags_checked_at = ""
    action = sync_from_amo_tags(amo, session, state, force=True)
    assert action != "start"
    assert session.human_handoff_active is True
    assert state.bot_may_reply() is False
    session = Session(chat_id="t", amo_lead_id=77)
    state = ConversationOwnershipState(chat_id="t")
    action = apply_amo_tags(
        ["Бот: стоп", "OBJ_X"],
        session, state, lead_id=77, pipeline_id=5,
    )
    assert action == "stop"
    assert session.human_handoff_active is True
    assert state.bot_may_reply() is False
    assert chat_is_paused(session, state)


def test_tag_start_resumes_and_clears_control_tags():
    amo = FakeAmo(["Бот: старт", "Бот: стоп", "OBJ_X"])
    session = Session(chat_id="t", amo_lead_id=77)
    session.human_handoff_active = True
    state = ConversationOwnershipState(
        chat_id="t",
        owner=ConversationOwner.HUMAN_HANDOFF,
        manager_takeover=True,
    )
    action = sync_from_amo_tags(amo, session, state, force=True)
    assert action == "start"
    assert session.human_handoff_active is False
    assert state.bot_may_reply() is True
    assert amo.replaced == ["OBJ_X"]
    assert "Бот: старт" not in amo.tags
    assert "Бот: стоп" not in amo.tags
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    turn = q.handle_message(session, "здравствуйте", {})
    assert not turn.silent


def test_hanging_start_after_c_does_not_resume():
    session = Session(chat_id="hang", amo_lead_id=11)
    state = ConversationOwnershipState(chat_id="hang")
    amo = FakeAmo(["Бот: старт"])
    amo.replace_lead_tags = MagicMock(side_effect=RuntimeError("no delete"))
    sync_from_amo_tags(amo, session, state, force=True)
    assert session.human_handoff_active is False
    assert session.amo_start_tag_present is True
    assert session.last_start_applied_at

    apply_external_outbound_stop(state, session=session, reason="C: chat", lead_id=11)
    assert session.human_handoff_active is True

    session.amo_tags_checked_at = ""
    action = sync_from_amo_tags(amo, session, state, force=True)
    assert action != "start"
    assert session.human_handoff_active is True
    assert state.bot_may_reply() is False


def test_readd_start_after_clear_resumes():
    session = Session(chat_id="re", amo_lead_id=12)
    state = ConversationOwnershipState(chat_id="re")
    amo = FakeAmo(["Бот: старт"])
    assert sync_from_amo_tags(amo, session, state, force=True) == "start"
    assert session.amo_start_tag_present is True

    apply_external_outbound_stop(state, session=session, reason="C: chat", lead_id=12)
    amo.tags = []
    session.amo_tags_checked_at = ""
    assert sync_from_amo_tags(amo, session, state, force=True) != "start"
    assert session.human_handoff_active is True

    amo.tags = ["Бот: старт"]
    session.amo_tags_checked_at = ""
    action = sync_from_amo_tags(amo, session, state, force=True)
    assert action == "start"
    assert session.human_handoff_active is False
    assert state.bot_may_reply() is True


def test_paused_process_client_message_is_full_zero():
    session = Session(chat_id="5041767749", amo_lead_id=77)
    session.human_handoff_active = True
    kwargs, events, *_rest = build_process_env(session=session)
    amo = kwargs["amo"]
    asyncio.run(process_client_message(**kwargs))
    assert "extract" not in events
    assert "handle_message" not in events
    assert "client_reply" not in events
    assert "amo_fields" not in events
    amo.update_lead_fields.assert_not_called()


def test_start_tag_on_paused_turn_allows_reply():
    session = Session(chat_id="5041767749", amo_lead_id=77)
    session.human_handoff_active = True
    amo = FakeAmo(["Бот: старт"])
    kwargs, events, *_rest = build_process_env(session=session)
    kwargs["amo"] = amo
    asyncio.run(process_client_message(**kwargs))
    assert session.human_handoff_active is False
    assert "extract" in events
    assert "client_reply" in events
    assert amo.replaced == []


def test_repair_handoff_not_swallowed_by_quiet_repair():
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    session = Session(chat_id="rp")
    assert q.handle_message(session, "????", {}).handoff_to_human is False
    assert q.handle_message(session, "???", {}).handoff_to_human is False
    third = q.handle_message(session, "??", {})
    assert third.handoff_to_human is True
    assert third.silent is False


def test_handoff_survives_session_reload(tmp_path):
    store = SessionStore(tmp_path)
    session = Session(chat_id="persist1", amo_lead_id=42)
    session.human_handoff_active = True
    session.handoff_to_human = True
    session.human_handoff_at = datetime.now(timezone.utc).isoformat()
    session.last_pause_at = session.human_handoff_at
    session.last_start_applied_at = "2019-01-01T00:00:00+00:00"
    session.last_start_absent_at = ""
    session.amo_start_tag_present = True
    session.cached_amo_tag_names = ["Бот: старт"]
    store.save(session)
    loaded = store.load("persist1")
    assert loaded is not None
    assert loaded.human_handoff_active is True
    assert loaded.last_pause_at
    assert loaded.amo_start_tag_present is True
    assert should_bot_respond(loaded) is False
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    assert q.handle_message(loaded, "нужны варианты", {}).silent

    resume_bot_manual(loaded, reason="resume via tag", lead_id=42)
    store.save(loaded)
    again = store.load("persist1")
    assert again is not None
    assert again.human_handoff_active is False
    assert again.last_start_applied_at
    assert should_bot_respond(again) is True
