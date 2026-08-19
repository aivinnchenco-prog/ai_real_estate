import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7.matching import budget_ok, find_alternatives, pets_ok
from agent7.models import Availability, LeadProfile, Listing, OwnerChannel
from agent7.notion_store import _to_listing
from agent7.object_id import extract_object_ids, extract_tg_post, extract_utm_campaign
from agent7.templates import owner_first_message


def make_listing(**kw) -> Listing:
    base = dict(object_id="20260701_001", title="Вилла", district="Раваи",
                housing_type="вилла", rooms=2, price_month=50000)
    base.update(kw)
    return Listing(**base)


# ---------- object_id ----------

def test_extract_ids_variants():
    text = "Смотрите #obj_20260708_001 и ещё #20260709_002, а также 20260710_003"
    assert extract_object_ids(text) == ["20260708_001", "20260709_002", "20260710_003"]


def test_extract_ids_dedup_and_empty():
    assert extract_object_ids("#obj_20260708_001 #obj_20260708_001") == ["20260708_001"]
    assert extract_object_ids("привет, ищу жильё") == []


def test_extract_tg_post():
    assert extract_tg_post("https://t.me/OpenHome_th/123") == ("OpenHome_th", 123)
    assert extract_tg_post("без ссылки") is None


def test_extract_utm_campaign():
    link = "https://t.me/OpenHome_th/99?utm_source=instagram&utm_campaign=A_20260713_003"
    assert extract_utm_campaign(link) == "A_20260713_003"
    assert extract_utm_campaign("hello") is None


# ---------- matching ----------

def test_budget_within_10_percent():
    lead = LeadProfile(budget=50000)
    assert budget_ok(55000, lead)          # ровно +10%
    assert not budget_ok(56000, lead)      # выше допуска
    lead.budget_tolerance_pct = 20         # клиент разрешил шире
    assert budget_ok(56000, lead)


def test_pets_empty_field_is_ok():
    lead = LeadProfile(pets=True)
    assert pets_ok(make_listing(pets_allowed=True), lead)
    assert pets_ok(make_listing(pets_allowed=None), lead)    # не указано — предлагаем
    assert not pets_ok(make_listing(pets_allowed=False), lead)


def test_busy_listing_excluded_unless_frees_up():
    lead = LeadProfile(check_in=date(2026, 8, 1), guests=2, budget=60000)
    busy = make_listing(object_id="A", availability=Availability.BUSY,
                        busy_until=date(2026, 9, 1))
    frees_up = make_listing(object_id="B", availability=Availability.BUSY,
                            busy_until=date(2026, 7, 20))
    free = make_listing(object_id="C", availability=Availability.FREE)
    result = find_alternatives([busy, frees_up, free], lead)
    ids = [l.object_id for l in result]
    assert "A" not in ids and "B" in ids and "C" in ids


def test_alternatives_sorted_by_similarity_to_chosen():
    chosen = make_listing(object_id="X", housing_type="вилла", rooms=2, district="Раваи")
    lead = LeadProfile(budget=60000, guests=2, preferred_object_id="X")
    similar = make_listing(object_id="S", housing_type="вилла", rooms=2, district="Раваи",
                           price_month=55000)
    other = make_listing(object_id="O", housing_type="кондо", rooms=1, district="Ката",
                         price_month=40000)
    result = find_alternatives([other, similar, chosen], lead, chosen=chosen)
    assert [l.object_id for l in result] == ["S", "O"]  # chosen исключён, похожий первым


def test_district_filter():
    lead = LeadProfile(districts=["Раваи"], budget=60000)
    rawai = make_listing(object_id="R", district="Раваи")
    kata = make_listing(object_id="K", district="Ката")
    assert [l.object_id for l in find_alternatives([rawai, kata], lead)] == ["R"]


# ---------- notion: чекбокс «Свободно» ----------

def notion_page(free_flag: bool, status: str = "") -> dict:
    props = {
        "Объект ID": {"type": "rich_text", "rich_text": [{"plain_text": "20260708_001"}]},
        "Свободно": {"type": "checkbox", "checkbox": free_flag},
    }
    if status:
        props["availability_status"] = {"type": "select", "select": {"name": status}}
    return {"id": "page1", "properties": props}


def test_free_checkbox_confirms_availability():
    assert _to_listing(notion_page(True)).availability == Availability.FREE


def test_free_checkbox_unchecked_means_unknown():
    assert _to_listing(notion_page(False)).availability == Availability.UNKNOWN


def test_busy_status_wins_over_checkbox():
    listing = _to_listing(notion_page(True, status="занято"))
    assert listing.availability == Availability.BUSY


# ---------- owner channel priority ----------

def test_owner_channel_priority():
    l = make_listing(owner_whatsapp="+66123", owner_telegram="@own",
                     source_url="https://airbnb.com/rooms/1")
    assert l.owner_channel() == (OwnerChannel.WHATSAPP, "+66123")
    l.owner_whatsapp = ""
    l.source_url = "https://facebook.com/marketplace/item/9"
    assert l.owner_channel() == (
        OwnerChannel.FB_MARKETPLACE,
        "https://facebook.com/marketplace/item/9",
    )
    l.source_url = "https://airbnb.com/rooms/1"
    assert l.owner_channel() is None


# ---------- alerts ----------

def test_alerts_without_token_prints_only(capsys, monkeypatch):
    monkeypatch.delenv("ERROR_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ERROR_CHAT_ID", raising=False)
    from agent7.alerts import notify_error
    assert notify_error("test", "boom") is False   # не настроен — только консоль
    assert "[ОШИБКА][test] boom" in capsys.readouterr().out


def test_alerts_dedup_window(monkeypatch):
    sent = []
    import agent7.alerts as alerts
    monkeypatch.setenv("ERROR_BOT_TOKEN", "t")
    monkeypatch.setenv("ERROR_CHAT_ID", "1")
    monkeypatch.setattr(alerts.requests, "post", lambda *a, **kw: sent.append(1))
    alerts._last_sent.clear()
    assert alerts.notify_error("comp", "same error") is True
    assert alerts.notify_error("comp", "same error") is False  # антиспам 5 минут
    assert alerts.notify_error("comp", "different error") is True
    assert len(sent) == 2


# ---------- owner scripts ----------

def test_airbnb_script_hides_client_and_contacts():
    msg = owner_first_message(OwnerChannel.AIRBNB, "01.08", "01.09")
    assert "клиент" not in msg.lower()
    assert "whatsapp" not in msg.lower()
    assert "Можно посмотреть дом" in msg


def test_fb_marketplace_asks_whatsapp():
    msg = owner_first_message(OwnerChannel.FB_MARKETPLACE, "01.08", "01.09", guests=2)
    assert "клиент" in msg.lower()
    assert "WhatsApp" in msg


def test_whatsapp_script_no_contact_request():
    msg = owner_first_message(OwnerChannel.WHATSAPP, "01.08", "01.09", guests=2)
    assert "клиент" in msg.lower()
    assert "WhatsApp для связи" not in msg


def test_owner_message_has_no_budget():
    """Бюджет клиента владельцу не сообщаем — внутренняя информация для торга."""
    msg = owner_first_message(OwnerChannel.TELEGRAM, "21.07.2026", "26.07.2026", guests=3)
    assert "Бюджет" not in msg
    assert "21.07.2026" in msg and "26.07.2026" in msg


def test_owner_message_includes_listing_link():
    """Ссылка на объявление владельца — сразу после «на ваше жильё»."""
    url = "https://www.airbnb.com/rooms/12345"
    msg = owner_first_message(OwnerChannel.WHATSAPP, "21.07.2026", "26.07.2026",
                              guests=3, listing_url=url)
    assert f"на ваше жильё {url}, готов заселиться" in msg
    # Без ссылки — фраза без лишних пробелов и скобок.
    msg2 = owner_first_message(OwnerChannel.WHATSAPP, "21.07.2026", "26.07.2026")
    assert "на ваше жильё, готов заселиться" in msg2


def test_owner_message_has_agency_intro():
    """Представляемся менеджером агентства (бренд из config/project.json)."""
    msg = owner_first_message(OwnerChannel.TELEGRAM, "21.07.2026", "26.07.2026")
    assert "Я менеджер от агентства" in msg
    assert "OpenHome" in msg


def test_owner_message_year_contract_without_checkout():
    """Нет даты выезда = годовой контракт: так и сообщаем владельцу."""
    msg = owner_first_message(OwnerChannel.WHATSAPP, "01.09.2026", "")
    assert "готов заселиться с 01.09.2026, контракт на год" in msg
