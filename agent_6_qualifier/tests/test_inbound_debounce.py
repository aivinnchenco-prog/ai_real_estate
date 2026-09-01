from datetime import datetime, timezone

from agent6_qualifier.messaging.inbound_debounce import (
    InboundDebounceBuffer,
    coalesce_messages,
)
from agent6_qualifier.messaging.types import CanonicalInboundMessage


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _msg(text: str, chat_id: str = "wa-1", mid: str = "1") -> CanonicalInboundMessage:
    return CanonicalInboundMessage(
        provider="wazzup",
        channel="whatsapp",
        message_id=mid,
        chat_id=chat_id,
        phone="995571237496",
        direction="inbound",
        text=text,
        timestamp=datetime.now(timezone.utc),
    )


def test_three_bubbles_in_two_seconds_are_one_turn():
    clock = _Clock()
    buf = InboundDebounceBuffer(window_sec=4.0, clock=clock.monotonic)
    buf.add(_msg("какая цена ?", mid="a"))
    clock.advance(0.7)
    buf.add(_msg("на какой период ?", mid="b"))
    clock.advance(0.7)
    buf.add(_msg("нужен ли депозит", mid="c"))
    clock.advance(2.0)
    assert buf.pop_ready() == []
    clock.advance(4.0)
    ready = buf.pop_ready()
    assert len(ready) == 1
    coal = coalesce_messages(ready[0])
    assert coal.text == "какая цена ?\nна какой период ?\nнужен ли депозит"
    assert coal.message_id == "c"
