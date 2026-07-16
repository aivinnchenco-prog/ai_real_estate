import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7.models import Availability, Listing
from agent7.qualifier import Qualifier, Session

CHOSEN = Listing(object_id="20260708_001", title="Вилла у моря", district="Раваи",
                 housing_type="вилла", rooms=2, price_month=50000,
                 tg_post_url="https://t.me/trip_home_phuket/10")
ALT = Listing(object_id="20260708_002", title="Вилла рядом", district="Раваи",
              housing_type="вилла", rooms=2, price_month=52000,
              tg_post_url="https://t.me/trip_home_phuket/11",
              photos_url="https://r2.example/20260708_002/photos")
BUSY = Listing(object_id="20260708_003", title="Занятая вилла", district="Раваи",
               housing_type="вилла", rooms=2, price_month=50000,
               availability=Availability.BUSY, busy_until=date(2026, 8, 15))


def make_qualifier(listings=None):
    listings = listings if listings is not None else [CHOSEN, ALT]
    by_id = {l.object_id: l for l in listings}
    return Qualifier(find_by_id=by_id.get, fetch_all=lambda: listings)


def test_object_id_message_confirms_and_asks_core():
    q = make_qualifier()
    s = Session(chat_id="1")
    turn = q.handle_message(s, "Здравствуйте! Интересует #obj_20260708_001", {})
    assert s.chosen is CHOSEN
    assert "Вилла у моря" in turn.reply_draft            # подтверждение объекта
    assert "t.me" not in turn.reply_draft                # ссылки НЕ в первом сообщении
    assert "даты" in turn.reply_draft                    # мягкий вопрос: даты + гости
    assert "бюджет" not in turn.reply_draft.lower()      # НЕ пачка вопросов сразу


def test_links_sent_after_core_fields_once():
    """Ссылки на пост и фото — после дат и гостей, и только один раз."""
    listing = Listing(object_id="20260708_009", title="Кондо", price_month=30000,
                      tg_post_url="https://t.me/trip_home_phuket/99",
                      photos_url="https://r2.example/20260708_009/photos/index.html")
    q = Qualifier(find_by_id={"20260708_009": listing}.get, fetch_all=lambda: [listing])
    s = Session(chat_id="1")
    turn = q.handle_message(s, "#20260708_009", {})
    assert "t.me" not in turn.reply_draft                # до квалификации ссылок нет
    turn = q.handle_message(s, "с 1 по 10 августа, двое", {
        "check_in": "2026-08-01", "check_out": "2026-08-10", "guests": 2})
    assert "t.me/trip_home_phuket/99" in turn.reply_draft
    assert "r2.example/20260708_009" in turn.reply_draft
    turn = q.handle_message(s, "бюджет 30к", {"budget": 30000, "districts": ["Ката"]})
    assert "t.me" not in turn.reply_draft                # повторно не шлём


def test_followup_phrase_after_core_fields():
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    turn = q.handle_message(s, "с 1 августа по 1 сентября, нас двое", {
        "check_in": "2026-08-01", "check_out": "2026-09-01", "guests": 2,
    })
    # добивка именно вашей фразой
    assert "другие варианты" in turn.reply_draft
    assert "район" in turn.reply_draft and "бюджет" in turn.reply_draft
    assert turn.skip_polish                       # фраза уходит без переписывания Gemini


def test_missing_checkout_is_asked_before_owner():
    """Названа только дата заезда — уточняем выезд, владельцу пока не пишем."""
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    turn = q.handle_message(s, "заеду 14 июля, нас двое", {
        "check_in": "2026-07-14", "guests": 2})
    assert "до какой даты" in turn.reply_draft
    assert not turn.need_owner_check
    # клиент назвал выезд -> обычный ход: запрос владельцу
    turn = q.handle_message(s, "до 21 июля, бюджет 70к, Бангтао", {
        "check_out": "2026-07-21", "budget": 70000, "districts": ["Бангтао"]})
    assert turn.need_owner_check


def test_full_qualification_triggers_owner_check():
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    q.handle_message(s, "даты и гости", {
        "check_in": "2026-08-01", "check_out": "2026-09-01", "guests": 2})
    turn = q.handle_message(s, "бюджет 55к, Раваи", {
        "budget": 55000, "districts": ["Раваи"]})
    assert turn.need_owner_check
    assert s.awaiting_alt_consent  # предложили посмотреть ещё варианты


def test_busy_object_reports_free_window():
    q = make_qualifier([BUSY, ALT])
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_003", {})
    q.handle_message(s, "…", {
        "check_in": "2026-08-01", "check_out": "2026-09-01", "guests": 2})
    turn = q.handle_message(s, "бюджет 55к, Раваи", {
        "budget": 55000, "districts": ["Раваи"]})
    assert "занят до 15.08.2026" in turn.reply_draft
    assert "свободен с 16.08.2026" in turn.reply_draft
    assert not turn.need_owner_check                     # владельцу пока не пишем


def test_short_consent_without_gemini_flag():
    """«да хочу» должно показывать альтернативы, даже если Gemini не понял согласие."""
    q = make_qualifier([BUSY, ALT])
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_003", {})
    q.handle_message(s, "…", {
        "check_in": "2026-08-01", "check_out": "2026-09-01", "guests": 2})
    q.handle_message(s, "бюджет 55к, Раваи", {"budget": 55000, "districts": ["Раваи"]})
    turn = q.handle_message(s, "да хочу", {})   # пустой update — Gemini «промолчал»
    assert "Вилла рядом" in turn.reply_draft


def test_decline_stops_alternative_offers():
    """«нет» после предложения альтернатив — не переспрашивать про варианты."""
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    q.handle_message(s, "…", {
        "check_in": "2026-08-01", "check_out": "2026-09-01", "guests": 2})
    q.handle_message(s, "бюджет 55к, Раваи", {"budget": 55000, "districts": ["Раваи"]})
    assert s.awaiting_alt_consent
    turn = q.handle_message(s, "нет", {})
    assert not s.awaiting_alt_consent
    assert "владельца" in turn.reply_draft            # ждём ответа владельца
    assert "вариант" not in turn.reply_draft.lower()  # не навязываем ещё раз


def test_only_this_object_skips_alternatives_offer():
    """«меня интересует только этот» — сразу к владельцу, без вопроса про варианты."""
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    q.handle_message(s, "…", {
        "check_in": "2026-08-01", "check_out": "2026-09-01", "guests": 2})
    turn = q.handle_message(s, "меня интересует только этот", {})
    assert turn.need_owner_check
    assert not s.awaiting_alt_consent
    assert s.only_chosen
    assert "похожих" not in turn.reply_draft


def test_likes_this_variant_skips_alternatives():
    """«мне нравится этот вариант» — без вопроса про альтернативы."""
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    q.handle_message(s, "14-го на 7 дней, двое", {
        "check_in": "2026-07-14", "check_out": "2026-07-21", "guests": 2})
    turn = q.handle_message(s, "мне нравится этот вариант", {})
    assert turn.need_owner_check
    assert s.only_chosen
    assert "похожих" not in turn.reply_draft.lower()
    assert "подбер" not in turn.reply_draft.lower()


def test_alternatives_shown_after_consent():
    q = make_qualifier([BUSY, ALT])
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_003", {})
    q.handle_message(s, "…", {
        "check_in": "2026-08-01", "check_out": "2026-09-01", "guests": 2})
    q.handle_message(s, "бюджет 55к, Раваи", {"budget": 55000, "districts": ["Раваи"]})
    turn = q.handle_message(s, "да, покажите", {"wants_alternatives": True})
    assert "20260708_002" in turn.reply_draft or "Вилла рядом" in turn.reply_draft
    assert "t.me/trip_home_phuket/11" in turn.reply_draft   # ссылка на TG-пост
    assert "r2.example" in turn.reply_draft                 # ссылка на фото R2


def test_no_owner_recheck_when_already_free():
    """После ответа владельца «свободно» не уходим к нему повторно."""
    q = make_qualifier()
    s = Session(chat_id="1")
    s.chosen = CHOSEN
    s.lead.preferred_object_id = "20260708_001"
    s.lead.check_in = __import__("datetime").date(2026, 7, 14)
    s.lead.check_out = __import__("datetime").date(2026, 7, 21)
    s.owner_verdict = "free"
    s.awaiting_owner = True  # устаревший флаг — должен сброситься
    s.only_chosen = True
    s.asked_core = True
    s.asked_followup = True
    s.links_sent = True
    turn = q.handle_message(s, "да готов", {})
    assert not turn.need_owner_check
    assert "владельц" not in turn.reply_draft.lower()
    assert "ФИО" in turn.reply_draft
    assert not s.awaiting_owner


def test_booking_confirmed_after_owner_free():
    q = make_qualifier()
    s = Session(chat_id="1")
    s.chosen = CHOSEN
    s.lead.preferred_object_id = "20260708_001"
    s.lead.check_in = __import__("datetime").date(2026, 7, 14)
    s.lead.check_out = __import__("datetime").date(2026, 7, 21)
    s.owner_verdict = "free"
    turn = q.handle_message(s, "да, подтверждаю", {})
    assert "ФИО" in turn.reply_draft
    assert not turn.booking_confirmed
    turn = q.handle_message(s, "Иванов Иван Петрович, гражданин России", {
        "full_name": "Иванов Иван Петрович", "citizenship": "Россия"})
    assert turn.booking_confirmed
    assert turn.handoff_to_human
    assert "менеджер" in turn.reply_draft.lower()
    assert "просмотр" in turn.reply_draft.lower()


def test_greeting_without_object_asks_object_or_search():
    """Клиент написал «просто так» — уточняем: конкретный объект или подбор."""
    q = make_qualifier()
    s = Session(chat_id="5")
    turn = q.handle_message(s, "Здравствуйте!", {})
    assert "конкретный объект" in turn.reply_draft
    assert "подобрать" in turn.reply_draft
    assert s.asked_object_source


def test_object_or_search_answer_selection():
    """Ответ «подберите» — идём в квалификацию (даты/гости)."""
    q = make_qualifier()
    s = Session(chat_id="5")
    q.handle_message(s, "Добрый день", {})
    turn = q.handle_message(s, "Подберите мне, пожалуйста, варианты", {})
    assert s.wants_selection
    assert "даты" in turn.reply_draft


def test_object_or_search_answer_specific_asks_link():
    """Ответ «видел у вас конкретный объект» — просим ссылку/номер."""
    q = make_qualifier()
    s = Session(chat_id="6")
    q.handle_message(s, "Привет", {})
    turn = q.handle_message(s, "Меня интересует конкретный объект, видел у вас на канале", {})
    assert "ссылку" in turn.reply_draft or "номер объекта" in turn.reply_draft
    # Прислал ID — обычный путь с подтверждением объекта.
    turn = q.handle_message(s, "#obj_20260708_001", {})
    assert s.chosen is CHOSEN
    assert "Вилла у моря" in turn.reply_draft


def test_no_object_goes_straight_to_matching():
    q = make_qualifier()
    s = Session(chat_id="2")
    turn = q.handle_message(s, "Ищу виллу на месяц", {})
    assert "даты" in turn.reply_draft
    turn = q.handle_message(s, "…", {
        "check_in": "2026-08-01", "check_out": "2026-09-01", "guests": 2})
    assert "другие варианты" in turn.reply_draft
    turn = q.handle_message(s, "Раваи, до 60к", {
        "budget": 60000, "districts": ["Раваи"]})
    # объект не выбран -> сразу подбор
    assert "Вилла" in turn.reply_draft
