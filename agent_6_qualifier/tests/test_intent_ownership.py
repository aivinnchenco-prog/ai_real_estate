"""Tests for human handoff TTL, intent classification, and search context reset."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.client_handler import process_client_message_sync
from agent6_qualifier.intent import classify_intent
from agent6_qualifier.session_ownership import (
    activate_human_handoff,
    handoff_ttl_hours,
    human_handoff_expired,
    should_bot_respond,
    touch_human_message,
)
from agent6_qualifier.models import Listing
from agent6_qualifier.qualifier import Qualifier, Session
from agent6_qualifier.search_context import archive_active_owner_request, find_pending_owner_request
from agent6_qualifier.sessions import SessionStore
from agent7_envoy.owner_result import apply_verdict_to_pending, OwnerVerdict

CHOSEN = Listing(object_id="F_20260809_001", title="Вилла", district="Раваи",
                 housing_type="вилла", rooms=2, price_month=50000)


def make_qualifier(listings=None):
    listings = listings if listings is not None else [CHOSEN]
    by_id = {l.object_id: l for l in listings}
    return Qualifier(find_by_id=by_id.get, fetch_all=lambda: listings)


def _awaiting_session() -> Session:
    s = Session(chat_id="wa1")
    s.chosen = CHOSEN
    s.lead.preferred_object_id = CHOSEN.object_id
    s.lead.check_in = date(2026, 8, 1)
    s.lead.guests = 2
    s.asked_core = True
    s.links_sent = True
    s.awaiting_owner = True
    s.last_outbound_template_key = "client_waiting_owner"
    return s


def test_a_new_house_not_wait_template():
    """A: owner pending + «новый дом» → NEW_PROPERTY_SEARCH, not wait-template."""
    q = make_qualifier()
    s = _awaiting_session()
    turn = q.handle_message(s, "Мне нужен новый дом", {})
    assert "уже отправил запрос владельцу" not in turn.reply_draft
    assert "подберём новый" in turn.reply_draft.lower() or "критерии" in turn.reply_draft.lower()
    assert not s.awaiting_owner
    assert find_pending_owner_request(s, "F_20260809_001") is not None


def test_b_continue_owner_status():
    """B: owner pending + status question → CONTINUE, wait-template ok."""
    q = make_qualifier()
    s = _awaiting_session()
    turn = q.handle_message(s, "Есть ответ от владельца?", {})
    assert "уже отправил запрос владельцу" in turn.reply_draft
    assert "F_20260809_001" in turn.reply_draft


def test_c_human_handoff_silent_under_ttl():
    """C: HUMAN_HANDOFF active <48h → bot silent."""
    s = Session(chat_id="tg1")
    activate_human_handoff(s)
    assert not should_bot_respond(s)
    q = make_qualifier()
    turn = q.handle_message(s, "Привет", {})
    assert turn.silent


def test_d_handoff_expired_new_search():
    """D: HUMAN_HANDOFF >48h + new search → bot resumes."""
    s = Session(chat_id="tg2")
    old = (datetime.now(timezone.utc) - timedelta(hours=handoff_ttl_hours() + 1)).isoformat()
    s.human_handoff_active = True
    s.human_handoff_at = old
    s.last_human_message_at = old
    assert human_handoff_expired(s)
    assert should_bot_respond(s)
    q = make_qualifier()
    turn = q.handle_message(s, "Хочу подобрать новый объект", {})
    assert not turn.silent
    assert s.wants_selection


def test_e_background_owner_response():
    """E: old owner response during new search — linked to old request."""
    s = _awaiting_session()
    archive_active_owner_request(s)
    s.awaiting_owner = False
    s.chosen = None
    s.lead.preferred_object_id = ""
    s.wants_selection = True
    s.lead.districts = ["Банг Тао"]

    verdict = OwnerVerdict(status="free")
    applied = apply_verdict_to_pending(s, "F_20260809_001", verdict)
    assert applied
    pending = find_pending_owner_request(s, "F_20260809_001")
    assert pending["owner_verdict"] == "free"
    assert not s.awaiting_owner
    assert s.wants_selection


def test_f_change_district_keeps_other_criteria():
    """F: change district only — guests/dates preserved."""
    q = make_qualifier()
    s = _awaiting_session()
    s.lead.guests = 4
    s.lead.check_in = date(2026, 9, 1)
    turn = q.handle_message(
        s,
        "Нужен дом в Банг Тао",
        {"districts": ["Банг Тао"]},
    )
    assert s.lead.guests == 4
    assert s.lead.check_in == date(2026, 9, 1)
    assert "Bang Tao" in s.lead.districts or "Банг Тао" in s.lead.districts or "Банг Тао" in turn.reply_draft


def test_g_supplied_criteria_not_reasked():
    """G: district + budget + bedrooms in one message — no full bullets re-ask."""
    q = make_qualifier()
    s = Session(chat_id="g1")
    s.awaiting_owner = True
    s.chosen = CHOSEN
    turn = q.handle_message(
        s,
        "Нужен дом в Банг Тао, 4 спальни, до 200 000 ฿",
        {"districts": ["Банг Тао"], "bedrooms": 4, "budget": 200000},
    )
    assert "Бюджет в месяц" not in turn.reply_draft
    assert s.lead.bedrooms == 4
    assert s.lead.budget == 200000


def test_h_wait_template_loop_prevented():
    """H: same wait-template loop → forced reclassification."""
    q = make_qualifier()
    s = _awaiting_session()
    turn1 = q.handle_message(s, "ну что?", {})
    assert "уже отправил" in turn1.reply_draft
    turn2 = q.handle_message(s, "Мне нужен новый дом", {})
    assert "уже отправил" not in turn2.reply_draft


def test_i_whatsapp_path():
    """I: WhatsApp path via shared client_handler."""
    q = make_qualifier()
    s = _awaiting_session()
    s.lead.source_channel = "whatsapp"
    result = process_client_message_sync(s, "Мне нужен новый дом", q, polish=False)
    assert not result.silent
    assert "уже отправил" not in result.reply


def test_j_telegram_path():
    """J: Telegram path via shared client_handler."""
    q = make_qualifier()
    s = _awaiting_session()
    s.lead.source_channel = "telegram"
    result = process_client_message_sync(s, "Что там по старому дому?", q, polish=False)
    assert not result.silent
    assert "уже отправил" in result.reply


def test_intent_classification_new_search():
    s = _awaiting_session()
    intent = classify_intent("Хочу подобрать новый объект по новым критериям", s, {}, use_llm=False)
    assert intent == "NEW_PROPERTY_SEARCH"


def test_session_store_pending_owner_lookup(tmp_path):
    store = SessionStore(tmp_path)
    s = _awaiting_session()
    archive_active_owner_request(s)
    s.awaiting_owner = False
    store.save(s)
    found = store.find_awaiting_owner_by_object("F_20260809_001")
    assert found is not None
    assert found.chat_id == "wa1"


def test_human_touch_extends_silence():
    s = Session(chat_id="h1")
    old = (datetime.now(timezone.utc) - timedelta(hours=handoff_ttl_hours() + 1)).isoformat()
    s.human_handoff_active = True
    s.human_handoff_at = old
    s.last_human_message_at = old
    assert human_handoff_expired(s)
    touch_human_message(s)
    assert not human_handoff_expired(s)
