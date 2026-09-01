import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.matching import PRICE_FLOOR, find_alternatives, price_sane
from agent6_qualifier.models import LeadProfile, Listing
from agent6_qualifier.templates import client_offer_line


def test_price_floor_constant():
    assert PRICE_FLOOR == 3000.0


def test_price_sane_drops_kwh_artefacts():
    broken = Listing(object_id="B", district="Laguna", price_month=7, rooms=2)
    ok = Listing(object_id="O", district="Laguna", price_month=40000, rooms=2)
    assert price_sane(broken) is False
    assert price_sane(ok) is True
    picked = find_alternatives([broken, ok], LeadProfile(guests=2), limit=5)
    assert [p.object_id for p in picked] == ["O"]


def test_price_sane_keeps_low_price_when_chips_exist():
    listing = Listing(
        object_id="C",
        district="Laguna",
        price_month=7,
        rooms=2,
        monthly_prices={"2026-10": {"price": 70000, "status": "monthly"}},
    )
    assert price_sane(listing) is True


def test_offer_line_hides_absurd_price():
    listing = Listing(
        object_id="B",
        title="Шамбала",
        district="Laguna",
        price_month=7,
        rooms=2,
    )
    line = client_offer_line(listing)
    assert "7" not in line
    assert "THB" not in line
