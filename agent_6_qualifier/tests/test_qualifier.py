import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.models import Availability, Listing
from agent6_qualifier.qualifier import Qualifier, Session

CHOSEN = Listing(object_id="20260708_001", title="Вилла у моря", district="Раваи",
                 housing_type="вилла", rooms=2, price_month=50000,
                 tg_post_url="https://t.me/OpenHome_th/10")
ALT = Listing(object_id="20260708_002", title="Вилла рядом", district="Раваи",
              housing_type="вилла", rooms=2, price_month=52000,
              tg_post_url="https://t.me/OpenHome_th/11",
              photos_url="https://r2.example/20260708_002/photos")
BUSY = Listing(object_id="20260708_003", title="Занятая вилла", district="Раваи",
               housing_type="вилла", rooms=2, price_month=50000,
               availability=Availability.BUSY, busy_until=date(2026, 8, 15))


def make_qualifier(listings=None):
    listings = listings if listings is not None else [CHOSEN, ALT]
    by_id = {l.object_id: l for l in listings}
    return Qualifier(find_by_id=by_id.get, fetch_all=lambda: listings)


def test_object_id_message_confirms_and_asks_bullets():
    """Подтверждение объекта фактами из таблицы + анкета одним сообщением."""
    q = make_qualifier()
    s = Session(chat_id="1")
    turn = q.handle_message(s, "Здравствуйте! Интересует #obj_20260708_001", {})
    assert s.chosen is CHOSEN
    assert "вилла" in turn.reply_draft                   # тип из таблицы
    assert "2 спальни" in turn.reply_draft               # спальни из таблицы
    assert "Раваи" in turn.reply_draft                   # район из таблицы
    assert "Бюджет в месяц" in turn.reply_draft          # анкета-критерии
    assert "Дата заезда" in turn.reply_draft
    assert "контракт на год" in turn.reply_draft         # выезд можно не указывать
    assert "t.me" not in turn.reply_draft                # ссылки НЕ в первом сообщении
    assert "THB" not in turn.reply_draft                 # цену до дат не называем


def test_links_sent_after_core_fields_once():
    """Ссылки на пост, фото и карту — после дат и гостей, и только один раз."""
    listing = Listing(object_id="20260708_009", title="Кондо", price_month=30000,
                      tg_post_url="https://t.me/OpenHome_th/99",
                      photos_url="https://r2.example/20260708_009/photos/index.html",
                      google_maps="https://maps.google.com/?q=7.99,98.29")
    q = Qualifier(find_by_id={"20260708_009": listing}.get, fetch_all=lambda: [listing])
    s = Session(chat_id="1")
    turn = q.handle_message(s, "#20260708_009", {})
    assert "t.me" not in turn.reply_draft                # до квалификации ссылок нет
    turn = q.handle_message(s, "с 1 по 10 августа, двое", {
        "check_in": "2026-08-01", "check_out": "2026-08-10", "guests": 2})
    assert "t.me/OpenHome_th/99" in turn.reply_draft
    assert "r2.example/20260708_009" in turn.reply_draft
    assert "maps.google.com" in turn.reply_draft         # локация Google Maps
    turn = q.handle_message(s, "бюджет 30к", {"budget": 30000, "districts": ["Ката"]})
    assert "t.me" not in turn.reply_draft                # повторно не шлём
    assert "maps.google.com" not in turn.reply_draft


def test_price_quoted_after_dates():
    """После дат клиент видит ориентировочную цену из monthly_prices/базовой."""
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    turn = q.handle_message(s, "с 1 августа, бюджет 55к, Раваи", {
        "check_in": "2026-08-01", "budget": 55000, "districts": ["Раваи"]})
    assert "50 000 THB/мес" in turn.reply_draft          # price_month из таблицы
    # цена не повторяется в следующих сообщениях
    assert s.price_quoted


def test_price_for_short_period_prorated():
    """Запрос меньше месяца — цена пропорцией на период клиента, не за месяц."""
    listing = Listing(
        object_id="20260708_010", title="Вилла", housing_type="вилла",
        rooms=3, district="Бангтао",
        monthly_prices={"2026-07": {"price": 130600, "status": "prorated"}},
    )
    q = Qualifier(find_by_id={"20260708_010": listing}.get, fetch_all=lambda: [listing])
    s = Session(chat_id="9")
    q.handle_message(s, "#20260708_010", {})
    turn = q.handle_message(s, "с 21 по 26 июля, бюджет 100к", {
        "check_in": "2026-07-21", "check_out": "2026-07-26", "budget": 100000})
    # 130600 / 30 × 5 ночей = 21766.7 → округление до сотен = 21 800
    assert "21 800 THB за 5 ночей" in turn.reply_draft
    assert "из расчёта 130 600 THB/мес" in turn.reply_draft
    assert "130 600 THB/мес." not in turn.reply_draft.split("за 5 ночей")[0]


def test_missing_checkout_means_year_contract():
    """Дата выезда не указана = контракт на год: не переспрашиваем, идём к владельцу."""
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    turn = q.handle_message(s, "заеду 14 июля, нас двое", {
        "check_in": "2026-07-14", "guests": 2})
    assert "до какой даты" not in turn.reply_draft
    assert turn.need_owner_check


def test_dates_missing_asks_only_dates():
    """Критерии без даты заезда — просим только дату, без повторной анкеты."""
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    turn = q.handle_message(s, "бюджет 60к, Раваи, 2 спальни", {
        "budget": 60000, "districts": ["Раваи"], "bedrooms": 2})
    assert "дату заезда" in turn.reply_draft
    assert "Бюджет в месяц" not in turn.reply_draft      # анкету не повторяем
    assert not turn.need_owner_check


def test_full_qualification_triggers_owner_check():
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    turn = q.handle_message(s, "с 1 августа по 1 сентября, бюджет 55к", {
        "check_in": "2026-08-01", "check_out": "2026-09-01", "budget": 55000})
    assert turn.need_owner_check
    assert s.awaiting_alt_consent  # предложили посмотреть ещё варианты
    assert "Вилла рядом" not in turn.reply_draft   # альтернативы ещё не показаны


def test_alternatives_not_listed_before_client_consent():
    """Список альтернатив не уходит клиенту до явного согласия."""
    q = make_qualifier([BUSY, ALT])
    s = Session(chat_id="alt1")
    q.handle_message(s, "#20260708_003", {})
    q.handle_message(s, "…", {
        "check_in": "2026-08-01", "check_out": "2026-09-01", "guests": 2})
    turn = q.handle_message(s, "бюджет 55к, Раваи", {"budget": 55000, "districts": ["Раваи"]})
    assert s.awaiting_alt_consent
    assert "20260708_002" not in turn.reply_draft
    assert "Вилла рядом" not in turn.reply_draft


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
    assert "владельца" in turn.reply_draft              # ждём ответа владельца
    assert "20260708_001" in turn.reply_draft           # метка объекта в сообщении
    assert "похожие" not in turn.reply_draft.lower()    # не навязываем ещё раз
    assert "подобрать" not in turn.reply_draft.lower()


def test_only_this_object_skips_alternatives_offer():
    """«только этот» вместе с датами — сразу к владельцу, без вопроса про варианты."""
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    turn = q.handle_message(
        s, "меня интересует только этот, с 1 августа по 1 сентября, нас двое", {
            "check_in": "2026-08-01", "check_out": "2026-09-01", "guests": 2})
    assert turn.need_owner_check
    assert not s.awaiting_alt_consent
    assert s.only_chosen
    assert "похожих" not in turn.reply_draft


def test_likes_this_variant_skips_alternatives():
    """«мне нравится этот вариант» — без вопроса про альтернативы."""
    q = make_qualifier()
    s = Session(chat_id="1")
    q.handle_message(s, "#20260708_001", {})
    turn = q.handle_message(s, "мне нравится этот вариант, 14-го на 7 дней, двое", {
        "check_in": "2026-07-14", "check_out": "2026-07-21", "guests": 2})
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
    assert "t.me/OpenHome_th/11" in turn.reply_draft   # ссылка на TG-пост
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
    assert "сколько человек" in turn.reply_draft.lower()   # первый вопрос брони
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
    # После одобрения владельца первым делом уточняем число проживающих.
    assert "сколько человек" in turn.reply_draft.lower()
    assert not turn.booking_confirmed
    turn = q.handle_message(s, "нас 4", {})
    assert s.lead.guests == 4                  # распарсено без LLM
    assert "ФИО" in turn.reply_draft
    assert not turn.booking_confirmed
    turn = q.handle_message(s, "Иванов Иван Петрович, гражданин России", {
        "full_name": "Иванов Иван Петрович", "citizenship": "Россия"})
    # После ФИО и гражданства — обязательный номер WhatsApp для связи.
    assert "WhatsApp" in turn.reply_draft
    assert not turn.booking_confirmed
    turn = q.handle_message(s, "+66 62 512-40-02", {})
    assert s.lead.whatsapp == "+66625124002"   # распарсен без LLM
    assert turn.booking_confirmed
    assert turn.handoff_to_human
    assert "гостей: 4" in " ".join(turn.events)
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
    """Ответ «подберите» — отправляем анкету-критерии."""
    q = make_qualifier()
    s = Session(chat_id="5")
    q.handle_message(s, "Добрый день", {})
    turn = q.handle_message(s, "Подберите мне, пожалуйста, варианты", {})
    assert s.wants_selection
    assert "Бюджет в месяц" in turn.reply_draft
    assert "Дата заезда" in turn.reply_draft


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
    assert "вилла" in turn.reply_draft and "Раваи" in turn.reply_draft


def test_unknown_object_id_reports_not_found():
    """Номер есть в сообщении, но объекта нет в базе — честный ответ, не вопрос про подбор."""
    q = make_qualifier()
    s = Session(chat_id="7")
    turn = q.handle_message(s, "Интересует объект 20260702_999", {})
    assert "20260702_999" in turn.reply_draft
    assert "Не нашёл" in turn.reply_draft
    assert not s.asked_object_source


def test_object_id_with_source_prefix():
    """ID с префиксом источника (A_...) распознаётся и находится в базе."""
    listing = Listing(object_id="A_20260713_003", title="Вилла Айрбнб", price_month=45000)
    q = Qualifier(find_by_id={"A_20260713_003": listing}.get, fetch_all=lambda: [listing])
    s = Session(chat_id="8")
    turn = q.handle_message(s, "Здравствуйте! Интересует A_20260713_003", {})
    assert s.chosen is listing
    assert "Вилла Айрбнб" in turn.reply_draft
    assert "Бюджет в месяц" in turn.reply_draft


def test_facebook_prefix_object_resolves():
    """Канонический F_ ID находится в базе и запускает анкету."""
    listing = Listing(object_id="F_20260708_001", title="FB вилла", housing_type="вилла",
                      district="Раваи", price_month=50000)
    q = Qualifier(find_by_id={"F_20260708_001": listing}.get, fetch_all=lambda: [listing])
    s = Session(chat_id="fb1")
    turn = q.handle_message(s, "Интересует F_20260708_001", {})
    assert s.chosen is listing
    assert "Бюджет в месяц" in turn.reply_draft


def test_utm_link_resolves_listing():
    listing = Listing(object_id="20260708_001", title="Вилла UTM", price_month=50000)
    q = Qualifier(find_by_id={"20260708_001": listing}.get, fetch_all=lambda: [listing])
    s = Session(chat_id="utm1")
    link = "https://t.me/OpenHome_th/55?utm_source=instagram&utm_campaign=20260708_001"
    turn = q.handle_message(s, f"Хочу арендовать {link}", {})
    assert s.chosen is listing
    assert "Вилла UTM" in turn.reply_draft


def test_no_object_goes_straight_to_matching():
    q = make_qualifier()
    s = Session(chat_id="2")
    turn = q.handle_message(s, "Ищу виллу на месяц", {})
    assert s.wants_selection                          # «ищу» = подбор, без переспроса
    assert "Бюджет в месяц" in turn.reply_draft       # анкета-критерии
    turn = q.handle_message(s, "с 1 августа, Раваи, до 60к, 2 спальни", {
        "check_in": "2026-08-01", "budget": 60000,
        "districts": ["Раваи"], "bedrooms": 2})
    # объект не выбран -> сразу подбор по критериям
    assert "Вилла" in turn.reply_draft
