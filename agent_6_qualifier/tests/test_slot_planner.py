import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.models import LeadProfile
from agent6_qualifier.qualifier import Qualifier, Session
from agent6_qualifier.slot_planner import Slot, plan_qualification
from agent6_qualifier.models import Listing


def test_open_search_mvc_requires_district_or_any():
    session = Session(chat_id="mvc")
    session.wants_selection = True
    session.lead = LeadProfile(
        check_in=date(2026, 10, 15), stay_months=3, guests=4,
    )
    plan = plan_qualification(session)
    assert plan.mvc_ready is False
    assert Slot.DISTRICTS in plan.next_slots

    session.lead.districts = ["Rawai"]
    assert plan_qualification(session).mvc_ready is True

    session.lead.districts = []
    session.lead.any_district = True
    assert plan_qualification(session).mvc_ready is True


def test_open_search_does_not_offer_without_district():
    listing = Listing(
        object_id="A_1", title="Вилла", district="Rawai",
        price_month=40000, rooms=2,
    )
    q = Qualifier(find_by_id={"A_1": listing}.get, fetch_all=lambda: [listing])
    session = Session(chat_id="gate")
    session.wants_selection = True
    session.asked_object_source = True
    session.lead.check_in = date(2026, 10, 15)
    session.lead.stay_months = 3
    session.lead.guests = 4
    turn = q.handle_message(
        session, "у вас есть варианты без без депозитов ?", {"asks_deposit": True},
    )
    assert "Вот что могу предложить" not in turn.reply_draft
    assert session.offered_alternatives is False
    assert "район" in turn.reply_draft.lower() or "депозит" in turn.reply_draft.lower()
