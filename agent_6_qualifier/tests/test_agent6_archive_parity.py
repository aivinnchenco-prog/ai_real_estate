"""Archive Agent6 business-logic parity (Telegram reference → shared core).

No live network. Uses deterministic qualification_hints (archive Gemini contract)
plus shared Qualifier — same path Telegram and WhatsApp adapters call.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.brain import apply_update
from agent6_qualifier.models import Listing
from agent6_qualifier.qualification_hints import (
    merge_hints,
    qualification_hints_from_text,
)
from agent6_qualifier.qualifier import Qualifier, Session
from agent6_qualifier.rental_policy import evaluate_rental_policy
from agent6_qualifier.session_extract import extract_lead_update_for_session
from agent6_qualifier.templates import CLIENT_ASK_DATES


FB = Listing(
    object_id="F_20260809_001",
    title="FB villa",
    district="Choeng Thale",
    housing_type="вилла",
    rooms=4,
    price_month=90000,
    source_url="https://www.facebook.com/marketplace/item/123",
    tg_post_url="https://t.me/OpenHome_th/1",
)
AIRBNB = Listing(
    object_id="A_20260809_001",
    title="Airbnb condo",
    district="Kata",
    housing_type="кондо",
    rooms=2,
    price_month=40000,
    source_url="https://www.airbnb.com/rooms/1",
)


def _q(listings):
    by_id = {l.object_id: l for l in listings}
    return Qualifier(find_by_id=by_id.get, fetch_all=lambda: list(listings))


def _step(q, s, text, *, today: date):
    upd = qualification_hints_from_text(text, today=today)
    return q.handle_message(s, text, upd)


def test_scenario_a_year_then_relative_check_in_no_repeat_ask():
    today = date(2026, 8, 10)
    q = _q([FB])
    s = Session(chat_id="wa_1")
    q.handle_message(s, "интересует объект F_20260809_001", {})
    assert s.chosen is FB
    assert s.asked_core is True

    turn = _step(q, s, "Хочу на год арендовать", today=today)
    assert s.lead.stay_months == 12.0
    assert "дату заезда" in turn.reply_draft

    turn = _step(q, s, "Через 5 дней хочу заехать", today=today)
    assert s.lead.check_in == today + timedelta(days=5)
    assert s.lead.stay_months == 12.0
    assert "дату заезда" not in turn.reply_draft
    assert turn.reply_draft != CLIENT_ASK_DATES


def test_scenario_b_exact_date_15_10_changes_next_question():
    today = date(2026, 8, 10)
    q = _q([FB])
    s = Session(chat_id="wa_2")
    q.handle_message(s, "F_20260809_001", {})
    _step(q, s, "на год", today=today)
    turn = _step(q, s, "15.10", today=today)
    assert s.lead.check_in == date(2026, 10, 15)
    assert "дату заезда" not in turn.reply_draft


def test_scenario_c_multi_field_one_message():
    today = date(2026, 8, 10)
    q = _q([FB])
    s = Session(chat_id="wa_3")
    q.handle_message(s, "F_20260809_001", {})
    text = "Нас двое, бюджет 150000, нужно 3 спальни"
    _step(q, s, text, today=today)
    assert s.lead.guests == 2
    assert s.lead.budget == 150000.0
    assert s.lead.bedrooms == 3


def test_scenario_d_out_of_order_accepted():
    today = date(2026, 8, 10)
    q = _q([FB])
    s = Session(chat_id="wa_4")
    q.handle_message(s, "F_20260809_001", {})
    _step(q, s, "бюджет 90000", today=today)
    assert s.lead.budget == 90000.0
    assert s.lead.check_in is None
    _step(q, s, "15.10", today=today)
    assert s.lead.check_in == date(2026, 10, 15)
    assert s.lead.budget == 90000.0


def test_scenario_e_correction_updates_state():
    s = Session(chat_id="wa_5")
    apply_update(s.lead, {"budget": 50000})
    apply_update(s.lead, {"budget": 120000})
    assert s.lead.budget == 120000.0


def test_scenario_f_object_id_after_qualification_preserves_fields():
    q = _q([FB, AIRBNB])
    s = Session(chat_id="wa_6")
    apply_update(s.lead, {"stay_months": 12, "check_in": "2026-10-15", "budget": 80000})
    s.asked_core = True
    s.asked_checkout = True
    s.asked_followup = True
    turn = q.handle_message(s, "интересует объект F_20260809_001", {})
    assert s.chosen is FB
    assert s.lead.stay_months == 12.0
    assert s.lead.check_in == date(2026, 10, 15)
    assert s.lead.budget == 80000.0
    assert "дату заезда" not in turn.reply_draft


def test_live_bug_full_sequence():
    today = date(2026, 8, 10)
    q = _q([FB])
    s = Session(chat_id="wa_live")
    _step(q, s, "Здравствуйте интересует объект F_20260809_001", today=today)
    assert s.lead.preferred_object_id == "F_20260809_001"
    _step(q, s, "Хочу на год арендовать", today=today)
    assert s.lead.stay_months == 12.0
    turn = _step(q, s, "Через 5 дней хочу заехать", today=today)
    assert s.lead.check_in == date(2026, 8, 15)
    assert "дату заезда" not in turn.reply_draft
    turn = _step(q, s, "15.10", today=today)
    assert s.lead.check_in == date(2026, 10, 15)
    assert s.lead.stay_months == 12.0
    assert "дату заезда" not in turn.reply_draft


def test_shared_extract_fills_hints_without_llm():
    s = Session(chat_id="ex")
    upd = extract_lead_update_for_session(
        "Через 5 дней хочу заехать, на год",
        s,
        use_llm=False,
    )
    assert upd.get("stay_months") == 12.0
    assert "check_in" in upd


def test_tg_and_wa_wrappers_delegate_to_shared_extract():
    from agent6_qualifier.messaging import wa_client_runtime as wa

    s = Session(chat_id="x")
    with patch(
        "agent6_qualifier.session_extract.extract_lead_update_for_session",
        return_value={"check_in": "2026-10-15"},
    ) as mocked:
        out = wa._extract_lead_update("15.10", s)
        assert mocked.called
        assert out["check_in"] == "2026-10-15"


def test_facebook_policy_pass_when_year_known():
    lead = Session(chat_id="p").lead
    lead.stay_months = 12
    lead.check_in = date(2026, 10, 15)
    pol = evaluate_rental_policy(FB, lead)
    assert pol.ok and pol.policy == "LONG_TERM_ONLY"
    assert not pol.needs_duration_clarification


def test_facebook_policy_blocks_explicit_short_stay():
    q = _q([FB])
    s = Session(chat_id="p2")
    q.handle_message(s, "F_20260809_001", {})
    q.handle_message(s, "…", {"check_in": "2026-10-15", "stay_months": 2})
    turn = q.handle_message(s, "ок", {})
    # after check_in known + short stay → policy prompt
    assert "6 месяцев" in turn.reply_draft or "Facebook" in turn.reply_draft


def test_airbnb_flexible():
    lead = Session(chat_id="p").lead
    lead.stay_months = 1
    pol = evaluate_rental_policy(AIRBNB, lead)
    assert pol.ok and pol.policy == "FLEXIBLE"


def test_apply_update_does_not_erase_missing_keys():
    s = Session(chat_id="m")
    apply_update(s.lead, {"check_in": "2026-10-15", "budget": 100000})
    apply_update(s.lead, {"guests": 2})
    assert s.lead.check_in == date(2026, 10, 15)
    assert s.lead.budget == 100000.0
    assert s.lead.guests == 2


def test_hints_do_not_override_llm_keys():
    merged = merge_hints(
        {"check_in": "2026-11-01", "stay_months": 12},
        {"check_in": "2026-08-15", "budget": 1},
    )
    assert merged["check_in"] == "2026-11-01"
    assert merged["budget"] == 1
