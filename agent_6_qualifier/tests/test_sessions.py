import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7.models import Availability, LeadProfile, Listing
from agent7.qualifier import Session
from agent7.sessions import SessionStore


def make_session() -> Session:
    s = Session(chat_id="12345")
    s.lead = LeadProfile(
        name="Андрей", check_in=date(2026, 8, 1), check_out=date(2026, 9, 1),
        budget=55000, districts=["Раваи"], guests=2, pets=True,
        preferred_object_id="20260702_001", source_channel="telegram",
    )
    s.chosen = Listing(
        object_id="20260702_001", title="Вилла", district="Раваи",
        price_month=70000, availability=Availability.BUSY,
        busy_until=date(2026, 7, 20),
        tg_post_url="https://t.me/trip_home_phuket/12",
    )
    s.asked_core = True
    s.asked_followup = True
    s.links_sent = True
    s.amo_lead_id = 32320181
    return s


def test_roundtrip_preserves_everything(tmp_path):
    store = SessionStore(tmp_path)
    store.save(make_session())
    loaded = store.load("12345")
    assert loaded is not None
    assert loaded.lead.name == "Андрей"
    assert loaded.lead.check_in == date(2026, 8, 1)
    assert loaded.lead.districts == ["Раваи"]
    assert loaded.lead.pets is True
    assert loaded.chosen.object_id == "20260702_001"
    assert loaded.chosen.availability == Availability.BUSY
    assert loaded.chosen.busy_until == date(2026, 7, 20)
    assert loaded.asked_core and loaded.asked_followup and loaded.links_sent
    assert loaded.amo_lead_id == 32320181


def test_load_missing_returns_none(tmp_path):
    assert SessionStore(tmp_path).load("нет-такого") is None


def test_corrupt_file_recovers(tmp_path):
    store = SessionStore(tmp_path)
    (tmp_path / "777.json").write_text("{сломанный json")
    assert store.load("777") is None
    assert (tmp_path / "777.corrupt").exists()   # убран с дороги, диалог начнётся заново


def test_dialog_continues_after_restart(tmp_path):
    """После «перезапуска» агент не задаёт вопросы заново."""
    from agent7.qualifier import Qualifier
    listing = make_session().chosen
    store = SessionStore(tmp_path)
    store.save(make_session())

    restored = store.load("12345")
    q = Qualifier(find_by_id={listing.object_id: listing}.get, fetch_all=lambda: [listing])
    turn = q.handle_message(restored, "ну что там?", {})
    draft = turn.reply_draft
    assert "на какие даты" not in draft          # даты уже известны
    assert "другие варианты" not in draft        # добивка уже была
    assert "t.me" not in draft                   # ссылки уже отправлены
