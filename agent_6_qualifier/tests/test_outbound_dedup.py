import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.client_handler import process_client_message
from agent6_qualifier.outbound_dedup import should_suppress_outbound
from agent6_qualifier.qualifier import Session, Turn
from client_runtime_fixtures import make_client_session, make_turn
from test_client_handler_service import build_process_env


def test_similar_recent_bot_reply_is_suppressed():
    session = Session(chat_id="dup")
    text = "Подскажите, пожалуйста, дату заезда. Сколько человек будет проживать?"
    session.history = [
        {"role": "assistant", "text": text},
        {"role": "user", "text": "ну"},
        {"role": "assistant", "text": text},
    ]
    assert should_suppress_outbound(session, text) is True
    assert should_suppress_outbound(session, "Какой район вам интересует?") is False


def test_handler_does_not_send_duplicate():
    session = make_client_session()
    reply = "Какой район вам интересует?"
    session.history = [{"role": "assistant", "text": reply}]
    turn = make_turn(reply_draft=reply, skip_polish=True)
    kwargs, events, *_ = build_process_env(session=session, turn=turn)
    asyncio.run(process_client_message(**kwargs))
    assert "client_reply" not in events
    assert session.history[-1]["role"] == "user"


def test_handler_sends_at_most_one_message():
    session = make_client_session()
    sends: list[str] = []

    async def send_twice(_event, reply):
        sends.append(reply)
        # A nested second send must be ignored by the one-shot wrapper.
        # The production wrapper lives inside process_client_message.

    turn = make_turn(reply_draft="Один вопрос про даты и гостей.", skip_polish=True)
    kwargs, events, *_ = build_process_env(session=session, turn=turn)
    kwargs["send_client_response"] = send_twice
    asyncio.run(process_client_message(**kwargs))
    assert sends == ["Один вопрос про даты и гостей."]
    assert events.count("client_reply") == 0  # replaced send hook
