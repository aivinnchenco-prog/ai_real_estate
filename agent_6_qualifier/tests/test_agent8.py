import json
import sys
import pytest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7.airbnb_check import CalendarCheck
from agent6_qualifier.models import LeadProfile, Listing, OwnerChannel
from agent6_qualifier.templates import client_object_busy
from agent8.outreach import OwnerBusyInfo, build_outreach_plan, precheck_alternatives

LEAD = LeadProfile(check_in=date(2026, 8, 1), check_out=date(2026, 9, 1),
                   guests=2, budget=50000)


def make_listing(**kw) -> Listing:
    base = dict(object_id="20260701_001", title="Вилла", price_month=50000)
    base.update(kw)
    return Listing(**base)


def checker_closed(url, ci, co):
    return CalendarCheck(available=False, blocked_ranges=[(ci, co)])


def checker_open(url, ci, co):
    return CalendarCheck(available=True, blocked_ranges=[])


# ---------- build_outreach_plan ----------

def test_airbnb_closed_dates_skips_owner():
    l = make_listing(source_url="https://airbnb.com/rooms/1",
                     owner_whatsapp="+66123")
    plan = build_outreach_plan(l, LEAD, checker=checker_closed)
    assert plan.channel is None
    assert "закрыты" in plan.skip_reason


def test_airbnb_open_dates_proceeds_via_whatsapp():
    l = make_listing(source_url="https://airbnb.com/rooms/1",
                     owner_whatsapp="+66123")
    plan = build_outreach_plan(l, LEAD, checker=checker_open)
    assert plan.channel == OwnerChannel.WHATSAPP
    assert "клиент" in plan.first_message.lower()
    assert "01.08.2026" in plan.first_message


def test_airbnb_dm_uses_special_script():
    l = make_listing(source_url="https://airbnb.com/rooms/1")  # контактов WA/TG нет
    plan = build_outreach_plan(l, LEAD, checker=checker_open)
    assert plan.channel == OwnerChannel.AIRBNB
    assert "Можно посмотреть дом" in plan.first_message
    assert "клиент" not in plan.first_message.lower()


def test_no_contacts_at_all():
    l = make_listing(source_url="")
    plan = build_outreach_plan(l, LEAD)
    assert plan.channel is None and "нет ни одного контакта" in plan.skip_reason


def test_empty_calendar_still_contacts_owner():
    """Пустая колонка «Календарь» — pre-check пропускается, владельцу пишем."""
    called = []

    def checker(url, ci, co):
        called.append(url)
        return CalendarCheck(available=False, blocked_ranges=[])

    l = make_listing(owner_whatsapp="+66123", calendar_url="")
    plan = build_outreach_plan(l, LEAD, checker=checker)
    assert called == []
    assert plan.channel == OwnerChannel.WHATSAPP
    assert plan.first_message


def test_telegram_contact_beats_airbnb_source():
    """WA/TG из колонок контактов всегда приоритетнее Airbnb DM."""
    l = make_listing(source_url="https://airbnb.com/rooms/1",
                     calendar_url="https://airbnb.com/rooms/1",
                     owner_telegram="@owner_tg")
    plan = build_outreach_plan(l, LEAD, checker=checker_open)
    assert plan.channel == OwnerChannel.TELEGRAM
    assert plan.contact == "@owner_tg"


def test_precheck_uses_calendar_column_not_source():
    """Проверка дат идёт по колонке «Календарь», даже если источник не Airbnb."""
    seen = {}

    def checker(url, ci, co):
        seen["url"] = url
        return CalendarCheck(available=True, blocked_ranges=[])

    l = make_listing(source_url="https://facebook.com/marketplace/item/2",
                     calendar_url="https://docs.google.com/spreadsheets/d/X#gid=1",
                     owner_whatsapp="+66123")
    plan = build_outreach_plan(l, LEAD, checker=checker)
    assert seen["url"] == "https://docs.google.com/spreadsheets/d/X#gid=1"
    assert plan.channel == OwnerChannel.WHATSAPP


# ---------- precheck_alternatives ----------

def test_precheck_drops_closed_airbnb_keeps_others():
    airbnb_closed = make_listing(object_id="A", source_url="https://airbnb.com/rooms/1")
    fb = make_listing(object_id="B", source_url="https://facebook.com/marketplace/item/2")
    results = {}

    def on_result(listing, check):
        results[listing.object_id] = check.available

    passed = precheck_alternatives([airbnb_closed, fb], LEAD,
                                   checker=checker_closed, on_result=on_result)
    assert [l.object_id for l in passed] == ["B"]
    assert results == {"A": False}  # результат записан (в проде -> Notion)


def test_precheck_without_dates_is_noop():
    lead = LeadProfile()  # даты ещё не собраны
    l = make_listing(source_url="https://airbnb.com/rooms/1")
    assert precheck_alternatives([l], lead, checker=checker_closed) == [l]


# ---------- занято -> сообщение клиенту ----------

def test_owner_busy_info_free_from_and_notion_update():
    info = OwnerBusyInfo(busy_until=date(2026, 8, 15), future_bookings="20-25 сентября")
    assert info.free_from == date(2026, 8, 16)
    upd = info.to_notion_update()
    assert upd["busy_until"] == date(2026, 8, 15)
    assert upd["future_bookings"] == "20-25 сентября"


def test_client_busy_message_offers_choice():
    msg = client_object_busy("A_20260713_003", "15.08.2026", "16.08.2026")
    assert "ваш вариант A_20260713_003" in msg
    assert "занят до 15.08.2026" in msg
    assert "свободен с 16.08.2026" in msg
    assert "похожие варианты" in msg


# ---------- результат проверки календаря -> Notion ----------

def test_notion_update_from_precheck_busy():
    from agent6_qualifier.models import Availability
    from agent8.calendar_check import notion_update_from_precheck

    check = CalendarCheck(
        available=False,
        blocked_ranges=[(date(2026, 7, 18), date(2026, 7, 19))],
        future_busy=[(date(2026, 7, 18), date(2026, 8, 2)),
                     (date(2026, 9, 1), date(2026, 9, 10))],
    )
    upd = notion_update_from_precheck(check, date(2026, 7, 14))
    assert upd["status"] == Availability.BUSY
    assert upd["busy_until"] == date(2026, 8, 2)   # конец мешающего периода
    assert "18.07–02.08.2026" in upd["future_bookings"]
    assert "01.09–10.09.2026" in upd["future_bookings"]


def test_notion_update_from_precheck_unknown_is_none():
    from agent8.calendar_check import notion_update_from_precheck
    check = CalendarCheck(available=None, blocked_ranges=[])
    assert notion_update_from_precheck(check, date(2026, 7, 14)) is None


# ---------- FB Marketplace -> WhatsApp ----------

def test_register_owner_whatsapp_saves_and_asks_calendar(monkeypatch):
    from agent8 import outreach

    saved = {}
    monkeypatch.setattr("agent6_qualifier.notion_store.save_owner_whatsapp",
                        lambda pid, wa: saved.update(page=pid, wa=wa))
    l = make_listing(page_id="page-1",
                     source_url="https://facebook.com/marketplace/item/2")
    msg = outreach.register_owner_whatsapp(l, "+66 89 000 11 22", LEAD)
    assert saved == {"page": "page-1", "wa": "+66 89 000 11 22"}
    assert l.owner_whatsapp == "+66 89 000 11 22"
    assert "01.08.2026" in msg                  # запрос по датам клиента
    assert "календарь" in msg.lower()           # вопрос про календарь объекта
    assert "Airbnb" in msg or "iCal" in msg


def test_register_owner_calendar_saves_url(monkeypatch):
    from agent8 import outreach

    saved = {}
    monkeypatch.setattr("agent6_qualifier.notion_store.save_calendar_url",
                        lambda pid, url: saved.update(page=pid, url=url))
    l = make_listing(page_id="page-2")
    outreach.register_owner_calendar(l, " https://cal.example/x.ics ")
    assert saved == {"page": "page-2", "url": "https://cal.example/x.ics"}
    assert l.calendar_url == "https://cal.example/x.ics"


# ---------- частичная доступность: свободно N ночей с даты заезда ----------

def test_free_nights_from_partial_window():
    # клиент: 14.07 -> 20.07; ночи 18-19.07 закрыты, дальше занято до 13.08
    check = CalendarCheck(
        available=False,
        blocked_ranges=[(date(2026, 7, 18), date(2026, 7, 19))],
        future_busy=[(date(2026, 7, 18), date(2026, 8, 13))],
    )
    assert check.free_nights_from(date(2026, 7, 14)) == 4
    assert check.busy_until(date(2026, 7, 14)) == date(2026, 8, 13)


def test_free_nights_zero_when_checkin_blocked():
    check = CalendarCheck(
        available=False,
        blocked_ranges=[(date(2026, 7, 14), date(2026, 7, 19))],
        future_busy=[(date(2026, 7, 1), date(2026, 8, 13))],
    )
    assert check.free_nights_from(date(2026, 7, 14)) == 0


def test_booking_doc_data_and_generation(tmp_path, monkeypatch):
    """Данные брони → JSON → node → docx. Годовой контракт без даты выезда."""
    from agent6_qualifier.qualifier import Session
    from agent6_qualifier.models import LeadProfile, Listing
    from agent8 import booking_doc

    monkeypatch.setattr("agent8_notary.booking_doc._OUT_DIR", tmp_path)

    def fake_run(cmd, **kwargs):
        assert cmd[0] == "node"
        assert Path(cmd[1]) == booking_doc._SCRIPT
        json_path = Path(cmd[2])
        out_path = Path(cmd[3])
        assert json_path.exists()
        assert out_path.suffix == ".docx"
        assert out_path.parent == tmp_path
        assert out_path.name.startswith("Бронь_A_20260713_003_")

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["object"]["objectId"] == "A_20260713_003"
        assert payload["client"]["fullName"] == "Иванов Иван"
        assert payload["client"]["citizenship"] == "Украина"
        assert payload["request"]["guests"] == "3"
        assert payload["request"]["checkinDate"] == "01.09.2026"
        assert payload["request"]["longTerm"] is True
        assert payload["request"]["checkoutDate"] == "01.09.2027"
        assert "годовой контракт" in payload["request"]["longTermNoteRu"]
        assert "Вилла у моря" not in payload["object"]["propertyName"]
        assert payload["object"]["propertyName"] == "Villa, 3 bedrooms / Вилла, 3 спальни"
        assert payload["agency"]["brand"] == "OpenHome"

        out_path.write_bytes(b"PK" + b"\x00" * 5000)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("agent8_notary.booking_doc.subprocess.run", fake_run)

    lead = LeadProfile(full_name="Иванов Иван", citizenship="Украина",
                       check_in=date(2026, 9, 1), budget=100000, guests=3)
    listing = Listing(object_id="A_20260713_003", title="Вилла у моря",
                      address="Тхаланг, Chang Wat Phuket",
                      housing_type="Вилла", rooms=3)
    session = Session(chat_id="1", lead=lead, chosen=listing)

    path = booking_doc.generate_booking_doc(session, "Telegram: @client")
    assert path.exists() and path.stat().st_size > 5000
    assert path.suffix == ".docx"
    assert path.parent == tmp_path
    assert path.name.startswith("Бронь_A_20260713_003_")


def test_booking_doc_path_sanitizes_object_id(tmp_path, monkeypatch):
    from agent6_qualifier.qualifier import Session
    from agent6_qualifier.models import LeadProfile, Listing
    from agent8 import booking_doc

    monkeypatch.setattr("agent8_notary.booking_doc._OUT_DIR", tmp_path)

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"PK" + b"\x00" * 6000)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("agent8_notary.booking_doc.subprocess.run", fake_run)

    lead = LeadProfile(check_in=date(2026, 9, 1), check_out=date(2026, 10, 1), guests=2)
    listing = Listing(object_id="weird/id", housing_type="вилла", rooms=2)
    session = Session(chat_id="1", lead=lead, chosen=listing)
    path = booking_doc.generate_booking_doc(session)
    assert "/" not in path.name
    assert "weird_id" in path.name


def test_booking_doc_generation_failure_does_not_leave_docx(tmp_path, monkeypatch):
    from agent6_qualifier.qualifier import Session
    from agent6_qualifier.models import LeadProfile, Listing
    from agent8 import booking_doc

    monkeypatch.setattr("agent8_notary.booking_doc._OUT_DIR", tmp_path)
    stale = tmp_path / "old_booking.docx"
    stale.write_bytes(b"stale")

    created_paths: list[Path] = []

    def fail_run(cmd, **kwargs):
        created_paths.append(Path(cmd[3]))
        class R:
            returncode = 1
            stdout = ""
            stderr = "boom"
        return R()

    monkeypatch.setattr("agent8_notary.booking_doc.subprocess.run", fail_run)

    session = Session(
        chat_id="1",
        lead=LeadProfile(check_in=date(2026, 9, 1), check_out=date(2026, 10, 1), guests=2),
        chosen=Listing(object_id="X_20260901_001", housing_type="вилла", rooms=2),
    )
    with pytest.raises(RuntimeError, match="generate_booking_request.js"):
        booking_doc.generate_booking_doc(session)
    assert len(created_paths) == 1
    assert not created_paths[0].exists()
    assert stale.exists() and stale.read_bytes() == b"stale"


def test_booking_doc_generation_success_without_file_raises(tmp_path, monkeypatch):
    """returncode=0 без выходного файла — ошибка, а не ложный успех."""
    from agent6_qualifier.qualifier import Session
    from agent6_qualifier.models import LeadProfile, Listing
    from agent8 import booking_doc

    monkeypatch.setattr("agent8_notary.booking_doc._OUT_DIR", tmp_path)

    created_paths: list[Path] = []

    def empty_success_run(cmd, **kwargs):
        created_paths.append(Path(cmd[3]))
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("agent8_notary.booking_doc.subprocess.run", empty_success_run)

    session = Session(
        chat_id="1",
        lead=LeadProfile(check_in=date(2026, 9, 1), check_out=date(2026, 10, 1), guests=2),
        chosen=Listing(object_id="Y_20260901_002", housing_type="вилла", rooms=2),
    )
    with pytest.raises(RuntimeError, match="generate_booking_request.js"):
        booking_doc.generate_booking_doc(session)
    assert len(created_paths) == 1
    assert not created_paths[0].exists()


def test_client_object_partial_message():
    from agent6_qualifier.templates import client_object_partial
    msg = client_object_partial("A_20260713_003", "14.07.2026", 4, "18.07.2026",
                                "13.08.2026", "14.08.2026")
    assert "вашему варианту A_20260713_003" in msg
    assert "14.07.2026" in msg and "4 дн" in msg
    assert "13.08.2026" in msg and "14.08.2026" in msg


# ---------- реестр владельцев ----------

def test_owner_registry_mark_and_get(tmp_path, monkeypatch):
    from agent7_envoy import owner_registry

    monkeypatch.setattr("agent7_envoy.owner_registry._PATH", tmp_path / "owners.json")

    assert owner_registry.get_owner("someone") is None
    owner_registry.mark_owner(tg_username="@Alexand_SMM", object_id="20260702_001")
    # регистронезависимо и без @
    reg = owner_registry.get_owner("alexand_smm")
    assert reg and reg["object_id"] == "20260702_001"
    # дообогащение chat_id не теряет object_id
    owner_registry.mark_owner(tg_username="alexand_smm", tg_chat_id="777")
    reg2 = owner_registry.get_owner("", "777")
    assert reg2 and reg2["object_id"] == "20260702_001"


def test_find_awaiting_owner_by_object(tmp_path):
    from agent6_qualifier.qualifier import Session
    from agent6_qualifier.sessions import SessionStore
    store = SessionStore(tmp_path)
    s = Session(chat_id="123")
    s.chosen = make_listing(object_id="20260702_001", owner_telegram="@x")
    s.awaiting_owner = True
    store.save(s)
    found = store.find_awaiting_owner_by_object("20260702_001")
    assert found is not None and found.chat_id == "123"
    assert store.find_awaiting_owner_by_object("nope") is None


# ---------- auto-outreach: текст занятости ----------

def test_busy_message_partial_window():
    from agent8.auto import busy_message_for_client
    lead = LeadProfile(check_in=date(2026, 7, 14), check_out=date(2026, 7, 20))
    check = CalendarCheck(
        available=False,
        blocked_ranges=[(date(2026, 7, 18), date(2026, 7, 19))],
        future_busy=[(date(2026, 7, 18), date(2026, 8, 13))],
    )
    msg = busy_message_for_client(check, make_listing(), lead)
    assert "14.07.2026" in msg and "4 дн" in msg and "14.08.2026" in msg


def test_busy_message_fully_blocked():
    from agent8.auto import busy_message_for_client
    lead = LeadProfile(check_in=date(2026, 7, 14), check_out=date(2026, 7, 20))
    check = CalendarCheck(
        available=False,
        blocked_ranges=[(date(2026, 7, 14), date(2026, 7, 19))],
        future_busy=[(date(2026, 7, 1), date(2026, 8, 13))],
    )
    msg = busy_message_for_client(check, make_listing(), lead)
    assert "занят до 13.08.2026" in msg and "свободен с 14.08.2026" in msg


def test_owner_busy_followup_asks_future_bookings():
    """Статус «занято» без сроков — уточняем дату освобождения и будущие брони."""
    info = OwnerBusyInfo()
    q = info.followup_question()
    assert "будущ" in q.lower() or "брони" in q.lower()
    assert "освобод" in q.lower() or "свобод" in q.lower()


def test_owner_busy_info_to_notion_update():
    info = OwnerBusyInfo(
        busy_until=date(2026, 8, 15),
        future_bookings="20–25 сентября",
    )
    upd = info.to_notion_update()
    from agent6_qualifier.models import Availability

    assert upd["status"] == Availability.BUSY
    assert upd["busy_until"] == date(2026, 8, 15)
    assert upd["future_bookings"] == "20–25 сентября"
