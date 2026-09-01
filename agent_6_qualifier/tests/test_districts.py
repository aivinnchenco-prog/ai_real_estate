import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.brain import apply_update
from agent6_qualifier.districts import (
    canonicalize_district,
    is_any_district_phrase,
)
from agent6_qualifier.matching import district_ok, find_alternatives
from agent6_qualifier.models import LeadProfile, Listing


def test_rawai_aliases_match_notion_rawai():
    listing = Listing(object_id="R", district="Rawai", price_month=40000, rooms=2)
    for raw in ("Равайи", "равай", "Раваи", "Rawai"):
        lead = LeadProfile()
        apply_update(lead, {"districts": [raw]})
        assert lead.districts == ["Rawai"], raw
        assert district_ok(listing, lead)


def test_kata_karon_stay_granular():
    lead = LeadProfile()
    apply_update(lead, {"districts": ["Ката", "Карон"]})
    assert lead.districts == ["Kata", "Karon"]
    kata = Listing(object_id="KA", district="Kata", price_month=40000, rooms=2)
    karon = Listing(object_id="KR", district="Karon", price_month=41000, rooms=2)
    laguna = Listing(object_id="L", district="Laguna", price_month=42000, rooms=2)
    picked = find_alternatives([kata, karon, laguna], lead, limit=5)
    assert {p.object_id for p in picked} == {"KA", "KR"}


def test_any_district_clears_hard_filter():
    assert is_any_district_phrase("Равайи или иные")
    assert is_any_district_phrase("Ката, карон предпочтительно но подойдет любой")
    lead = LeadProfile()
    apply_update(
        lead,
        {"districts": ["Равайи"]},
        message="Равайи или иные",
    )
    assert lead.districts == ["Rawai"]
    assert lead.any_district is True
    layan = Listing(object_id="LY", district="Layan", price_month=40000, rooms=2)
    assert district_ok(layan, lead)


def test_unresolvable_district_is_not_written():
    lead = LeadProfile()
    apply_update(lead, {"districts": ["Марс", "предпочтительно"]})
    assert lead.districts == []
