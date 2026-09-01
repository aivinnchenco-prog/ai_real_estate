"""Replay A102/A103 with frozen today=2026-08-26 (docs/A102_A103_replay.json)."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.amo import AmoClient
from agent6_qualifier.brain import apply_update
from agent6_qualifier.matching import district_ok, find_alternatives, price_sane
from agent6_qualifier.models import LeadProfile, Listing
from agent6_qualifier.qualifier import Qualifier, Session
from agent6_qualifier.templates import CLIENT_ASK_BUDGET_TOLERANCE

FROZEN_TODAY = date(2026, 8, 26)
DOCS = Path(__file__).resolve().parents[1] / "docs" / "A102_A103_replay.json"


def _incident_pool() -> list[Listing]:
    return [
        Listing(object_id="A_20260717_006", title="kWh artefact",
                district="Laguna", price_month=7, rooms=2),
        Listing(object_id="A_20260717_005", title="kWh artefact 8",
                district="Thalang", price_month=8, rooms=2),
        Listing(object_id="A_20260717_002", title="deposit as rent",
                district="Layan", price_month=5000, rooms=2),
        Listing(object_id="RAWAI_OK", title="Rawai villa",
                district="Rawai", price_month=45000, rooms=2, deposit=15000),
        Listing(object_id="KATA_OK", title="Kata villa",
                district="Kata", price_month=40000, rooms=2),
        Listing(object_id="KARON_OK", title="Karon villa",
                district="Karon", price_month=42000, rooms=2),
        Listing(object_id="BANG_OK", title="Bang Tao villa",
                district="Bang Tao", price_month=55000, rooms=2),
    ]


def test_replay_dump_frozen_today():
    data = json.loads(DOCS.read_text(encoding="utf-8"))
    assert data["frozen_today"] == "2026-08-26"
    assert FROZEN_TODAY.isoformat() == data["frozen_today"]


def test_a102_rawai_matches_and_hides_absurd_prices():
    pool = _incident_pool()
    lead = LeadProfile(check_in=date(2026, 8, 28), check_out=date(2026, 9, 28),
                       guests=4, bedrooms=2)
    apply_update(lead, {"districts": ["Равайи"]}, message="Район: Равайи")
    assert lead.districts == ["Rawai"]
    picked = find_alternatives(pool, lead, limit=10)
    ids = {l.object_id for l in picked}
    assert "RAWAI_OK" in ids
    assert "A_20260717_006" not in ids
    assert "A_20260717_005" not in ids
    assert all(price_sane(l) for l in picked)
    assert all((l.price_month or 0) >= 3000 for l in picked)


def test_a103_kata_karon_any_does_not_bleed_into_all_karon():
    pool = _incident_pool()
    lead = LeadProfile(check_in=date(2026, 10, 15), stay_months=3, guests=4, pets=True)
    apply_update(
        lead,
        {"districts": ["Ката", "Карон"]},
        message="Ката , карон предпочтительно но подойдет любой",
    )
    assert lead.districts == ["Kata", "Karon"]
    assert lead.any_district is True
    # Soft preference kept, hard filter off — all sane listings may pass.
    assert district_ok(Listing(object_id="x", district="Rawai"), lead)


def test_a102_or_other_areas_lifts_hard_district_filter():
    lead = LeadProfile()
    apply_update(lead, {"districts": ["Равайи"]}, message="Равайи или иные")
    assert lead.districts == ["Rawai"]
    assert lead.any_district is True


def test_replay_empty_shortlist_is_not_budget_when_budget_unknown():
    pool = [
        Listing(object_id="K1", title="Kata only", district="Kata",
                price_month=40000, rooms=2),
    ]
    q = Qualifier(find_by_id={"K1": pool[0]}.get, fetch_all=lambda: pool)
    session = Session(chat_id="a102")
    session.wants_selection = True
    session.asked_object_source = True
    turn = q.handle_message(
        session,
        "Можно и варианты. Район: Равайи. С 28.08 по 28.09, 4 человека.",
        {
            "districts": ["Равайи"],
            "check_in": "2026-08-28",
            "check_out": "2026-09-28",
            "guests": 4,
            "wants_alternatives": True,
        },
    )
    assert session.lead.districts == ["Rawai"]
    assert session.lead.budget is None
    assert CLIENT_ASK_BUDGET_TOLERANCE not in turn.reply_draft
    assert "бюджет" not in turn.reply_draft.lower()
    assert "район" in turn.reply_draft.lower() or "дат" in turn.reply_draft.lower()
    assert "7" not in turn.reply_draft
    assert "8 THB" not in turn.reply_draft


def test_replay_too_cheap_and_deposit():
    listing = Listing(
        object_id="RAWAI_OK", title="Rawai villa", district="Rawai",
        price_month=45000, rooms=2, deposit=15000,
    )
    q = Qualifier(find_by_id={"RAWAI_OK": listing}.get, fetch_all=lambda: [listing])
    cheap = Session(chat_id="a103-cheap")
    cheap.wants_selection = True
    cheap.lead.districts = ["Rawai"]
    cheap.lead.check_in = date(2026, 10, 15)
    cheap.lead.guests = 4
    cheap.shown_object_ids = ["RAWAI_OK"]
    turn = q.handle_message(cheap, "почему так дешево ? номера студии от 15.000", {})
    assert "Вот что могу предложить" not in turn.reply_draft
    assert "бюджет" not in turn.reply_draft.lower() or "длинн" in turn.reply_draft.lower()

    dep = Session(chat_id="a103-dep")
    dep.chosen = listing
    dep.lead.check_in = date(2026, 10, 15)
    turn = q.handle_message(dep, "нужен ли депозит", {"asks_deposit": True})
    assert "15 000" in turn.reply_draft


def test_amo_payload_for_replay_lead_never_sends_price_zero():
    amo = AmoClient.__new__(AmoClient)
    field_ids = {
        "Район": 1, "Гостей": 2, "Животные": 3,
        "Дата заезда": 4, "Дата выезда": 5,
    }
    lead = LeadProfile(
        districts=["Rawai"], guests=4, pets=True,
        check_in=date(2026, 10, 15), check_out=date(2027, 1, 15),
    )
    body = amo.build_lead_fields_payload(lead, field_ids)
    values = {item["field_id"]: item["values"][0]["value"]
              for item in body["custom_fields_values"]}
    assert values[1] == "Rawai"
    assert values[2] == 4
    assert values[3] == "да"
    assert "price" not in body
