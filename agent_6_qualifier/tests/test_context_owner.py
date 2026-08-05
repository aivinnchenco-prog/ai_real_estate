import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.context import build_knowledge, dialog_stage, format_history
from agent6_qualifier.models import Listing
from agent6_qualifier.qualifier import Session
from agent8.owner_result import OwnerVerdict, build_client_message


def test_dialog_stage_awaiting_owner():
    s = Session(chat_id="1")
    s.awaiting_owner = True
    assert dialog_stage(s) == "ожидание ответа владельца"


def test_knowledge_includes_object_and_dates():
    s = Session(chat_id="1")
    s.chosen = Listing(object_id="20260702_001", title="Вилла", district="Раваи", price_month=70000)
    s.lead.preferred_object_id = "20260702_001"
    s.lead.check_in = date(2026, 7, 14)
    s.lead.check_out = date(2026, 7, 21)
    s.lead.guests = 2
    s.only_chosen = True
    kb = build_knowledge(s)
    assert "20260702_001" in kb
    assert "только выбранный объект" in kb
    assert "2026-07-14" in kb


def test_format_history():
    hist = [
        {"role": "user", "text": "Интересует объект"},
        {"role": "assistant", "text": "На какие даты?"},
    ]
    assert "Клиент" in format_history(hist)
    assert "Агент" in format_history(hist)


def test_owner_free_message():
    s = Session(chat_id="1")
    s.chosen = Listing(object_id="X", title="Вилла у моря")
    s.lead.check_in = date(2026, 7, 14)
    s.lead.check_out = date(2026, 7, 21)
    msg = build_client_message(OwnerVerdict(status="free"), s)
    assert "подтвердил" in msg.lower()
    assert "14.07" in msg


def test_owner_busy_message():
    s = Session(chat_id="1")
    s.chosen = Listing(object_id="X", title="Вилла")
    msg = build_client_message(
        OwnerVerdict(status="busy", busy_until=date(2026, 7, 20)), s)
    assert "занят" in msg.lower()
    assert "21.07.2026" in msg


def test_owner_conditions_message():
    s = Session(chat_id="1")
    s.chosen = Listing(object_id="X", title="Вилла")
    msg = build_client_message(
        OwnerVerdict(status="conditions_changed", new_price_month=75000,
                     conditions_note="заезд с 16 июля"), s)
    assert "изменились" in msg.lower()
    assert "75" in msg


def test_busy_verdict_updates_notion_availability():
    from agent6_qualifier.models import Availability
    from agent8.owner_result import OwnerVerdict, notion_availability_update

    v = OwnerVerdict(
        status="busy",
        busy_until=date(2026, 8, 15),
        future_bookings="20–25 сентября",
    )
    upd = notion_availability_update(v)
    assert upd["status"] == Availability.BUSY
    assert upd["busy_until"] == date(2026, 8, 15)
    assert upd["future_bookings"] == "20–25 сентября"
