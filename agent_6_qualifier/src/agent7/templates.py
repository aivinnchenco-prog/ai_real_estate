"""Шаблоны сообщений. Gemini адаптирует тон/язык, но смысл и структура — отсюда.

Диалог с клиентом — мягкий и короткий: сначала только даты и количество человек,
район и бюджет добираем одной фразой-добивкой, остальное — точечно по ходу.

Тексты можно менять без правки кода: любые ключи из _DEFAULT_TEMPLATES
переопределяются в config/templates.json (плейсхолдеры вида {title} сохранять).
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Listing, OwnerChannel

_DEFAULT_TEMPLATES: dict[str, str] = {
    # ---------- Клиент (Agent 7) ----------
    # Клиент написал без объекта и без параметров поиска — выясняем, что ему нужно.
    "client_ask_object_or_search": (
        "Здравствуйте! Подскажите, пожалуйста: вас интересует конкретный объект, "
        "который вы увидели на наших ресурсах, — или подобрать для вас "
        "варианты под ваш запрос?"
    ),
    "client_ask_object_link": (
        "Отлично! Пришлите, пожалуйста, ссылку на пост или номер объекта "
        "(например, #A_20260713_003) — сразу посмотрю по нему всю информацию."
    ),
    # Номер объекта в сообщении есть, но в базе такого нет (опечатка/старый пост).
    "client_object_not_found": (
        "Не нашёл объект с номером {object_id} в нашей базе. Проверьте, "
        "пожалуйста, номер или пришлите ссылку на пост — сразу посмотрю. "
        "Могу также подобрать варианты под ваш запрос."
    ),
    # Единый вопрос-анкета: все критерии одним сообщением, меньше переписки.
    "client_qualify_bullets": (
        "Сообщите, пожалуйста:\n"
        "• Бюджет в месяц\n"
        "• Район\n"
        "• Количество спален\n"
        "• Дата заезда\n"
        "• Дата выезда (не указывайте, если контракт на год)"
    ),
    # Минимум для проверки доступности и цены — дата заезда.
    "client_ask_dates": (
        "Уточните, пожалуйста, дату заезда — проверю доступность и назову "
        "цену на ваш месяц."
    ),
    # Ориентировочная цена месяца заезда из monthly_prices.
    "client_price_line": "Ориентировочная цена на ваши даты: {quote}.",
    "client_ask_alternatives": (
        "Хотите, подберу для вас ещё несколько похожих вариантов?"
    ),
    "client_ask_budget_tolerance": (
        "Пока в рамках бюджета вариантов мало. Подскажите, какая погрешность "
        "по бюджету для вас допустима — рассмотрим чуть шире?"
    ),
    "client_no_alternatives": (
        "Пока похожих вариантов по вашим критериям в базе нет — "
        "сосредоточусь на вашем варианте {object_id}. "
        "Как только владелец ответит по доступности, сразу напишу вам."
    ),
    "client_wait_owner": (
        "Хорошо! Уточняю у владельца доступность вашего варианта {object_id} "
        "на ваши даты и сразу вернусь к вам с ответом."
    ),
    "client_waiting_owner": (
        "Я уже отправил запрос владельцу и жду ответа по вашему варианту "
        "{object_id}. Как только узнаю — сразу напишу вам."
    ),
    # Один вопрос — один ответ: сначала только ФИО, гражданство — следующим
    # сообщением. Если клиент прислал всё сразу, второй вопрос не задаётся.
    "client_booking_fio": (
        "Отлично! Для оформления брони подскажите, пожалуйста, "
        "ваше ФИО (фамилия, имя, отчество)."
    ),
    "client_booking_citizenship": (
        "Спасибо! И укажите, пожалуйста, ваше гражданство."
    ),
    "client_booking_whatsapp": (
        "Принято! И оставьте, пожалуйста, номер телефона WhatsApp для связи "
        "(в формате +66...)."
    ),
    # Первый вопрос после согласия на бронь: сколько человек заедет
    # (число идёт в amoCRM «Гостей» и в договор, п. 3.2).
    "client_booking_guests": (
        "Отлично! Уточните, пожалуйста, сколько человек будет проживать?"
    ),
    # Название объекта клиенту не вписываем (парсерные заголовки длинные и
    # корявые) — говорим «ваш вариант {object_id}»: метка объекта помогает
    # клиенту и боту ссылаться на один и тот же объект в переписке.
    "client_booking_confirmed": (
        "Бронь по вашему варианту {object_id} зафиксирована на {date_range}. "
        "Наш менеджер сейчас подключится к диалогу и согласует с вами "
        "время просмотра жилья вместе с владельцем."
    ),
    "client_handoff_wait": (
        "Наш менеджер уже видит вашу заявку и скоро напишет вам "
        "для назначения просмотра. Ожидайте, пожалуйста."
    ),
    "client_new_search_prompt": (
        "Конечно. Давайте подберём новый вариант. Что хотите изменить в критериях?"
    ),
    "client_new_search_with_criteria": (
        "Конечно, подберём новый вариант. Учту ваши критерии: {criteria_summary}. "
        "Уточните, пожалуйста, что ещё важно — или сразу назовите дату заезда, "
        "если ещё не указали."
    ),
    "client_owner_confirmed": (
        "Отличные новости! Владелец подтвердил, что ваш вариант {object_id} "
        "свободен на ваши даты ({date_range}). Подтверждаете бронь?"
    ),
    "client_owner_conditions": (
        "Владелец по вашему варианту {object_id} ответил, но условия немного "
        "изменились: {note}. Подходят ли вам такие условия?"
    ),
    "client_object_busy": (
        "К сожалению, ваш вариант {object_id} занят до {busy_until} и будет "
        "свободен с {free_from}. "
        "Подойдут ли вам такие даты? Если нет — подберу похожие варианты."
    ),
    "client_object_partial": (
        "По вашему варианту {object_id}: с вашей желаемой даты заезда {check_in} "
        "свободно всего {free_nights} дн. — выезд не позднее {free_until}. "
        "Дальше занято до {busy_until}, а полностью свободные даты — с {free_from}. "
        "Как вам удобнее: короткое заселение с {check_in} на {free_nights} дн. "
        "или заезд с {free_from}? Могу также подобрать похожие варианты."
    ),
    # ---------- Собственник (Agent 8) ----------
    # Airbnb — НЕ про клиента и без запроса контакта (см. owner_airbnb_step2).
    "owner_first_airbnb": "Здравствуйте! Меня интересует ваше жильё. Можно посмотреть дом?",
    # {listing_ref} — ссылка на объявление владельца (Airbnb/FB), чтобы он
    # сразу понял, о каком объекте речь; пустая строка, если ссылки нет.
    # {agency} — бренд агентства из config/project.json (корень проекта).
    "owner_first_base": (
        "Здравствуйте! Я менеджер от агентства {agency}! "
        "У меня есть клиент на ваше жильё{listing_ref}, "
        "готов заселиться с {check_in} по {check_out}."
    ),
    # Клиент не назвал дату выезда = годовой контракт: владелец должен понимать
    # это сразу — годовой срок вносится и в договор.
    "owner_first_base_year": (
        "Здравствуйте! Я менеджер от агентства {agency}! "
        "У меня есть клиент на ваше жильё{listing_ref}, "
        "готов заселиться с {check_in}, контракт на год."
    ),
    "owner_first_guests": " Гостей: {guests}.",
    "owner_first_ask": " Подскажите, свободны ли эти даты?",
    "owner_first_fb_whatsapp": (
        " Оставьте, пожалуйста, ваш WhatsApp для связи — так будет быстрее."
    ),
    # Airbnb, шаг 2 (после ответа владельца): контакт напрямую не просим,
    # наши контакты — на изображении-аватаре аккаунта.
    "owner_airbnb_step2": "Посмотрите моё изображение-аватар.",
    # Если владелец сообщил «занято» — обязательные уточнения для availability в Notion.
    "owner_busy_followup": (
        "Понял вас. Подскажите, до какого числа занято и когда освободится? "
        "Есть ли брони в будущем и на какие даты? Буду предлагать вам клиентов на свободные окна."
    ),
    "owner_ack_free": (
        "Отлично, спасибо! Передаю клиенту и вернусь к вам с подтверждением брони."
    ),
    "owner_ack_conditions": (
        "Спасибо! Передам клиенту новые условия и вернусь с ответом."
    ),
    # Первое сообщение в WhatsApp владельцу, чей контакт получен в FB Marketplace:
    # заодно спрашиваем календарь объекта, чтобы дальше проверять даты без переписки.
    "owner_wa_ask_calendar": (
        "И ещё вопрос: есть ли у вас календарь занятости объекта — ссылка Airbnb, "
        "iCal или Google-таблица? Пришлите её, пожалуйста, — буду сверять даты сам "
        "и беспокоить вас только по свободным окнам."
    ),
}


def _load_templates() -> dict[str, str]:
    override = Path(__file__).resolve().parents[2] / "config" / "templates.json"
    templates = dict(_DEFAULT_TEMPLATES)
    if override.exists():
        try:
            data = json.loads(override.read_text(encoding="utf-8"))
            templates.update({k: v for k, v in data.items() if k in _DEFAULT_TEMPLATES})
        except (OSError, ValueError):
            pass
    return templates


_T = _load_templates()

# ---------- Клиент (Agent 7) ----------

CLIENT_ASK_OBJECT_OR_SEARCH = _T["client_ask_object_or_search"]
CLIENT_ASK_OBJECT_LINK = _T["client_ask_object_link"]


def client_object_not_found(object_id: str) -> str:
    return _T["client_object_not_found"].format(object_id=object_id)
CLIENT_QUALIFY_BULLETS = _T["client_qualify_bullets"]
CLIENT_ASK_DATES = _T["client_ask_dates"]


def client_price_line(quote: str) -> str:
    return _T["client_price_line"].format(quote=quote)
CLIENT_ASK_ALTERNATIVES = _T["client_ask_alternatives"]
CLIENT_ASK_BUDGET_TOLERANCE = _T["client_ask_budget_tolerance"]
CLIENT_BOOKING_FIO = _T["client_booking_fio"]
CLIENT_BOOKING_CITIZENSHIP = _T["client_booking_citizenship"]
CLIENT_BOOKING_WHATSAPP = _T["client_booking_whatsapp"]
CLIENT_BOOKING_GUESTS = _T["client_booking_guests"]
CLIENT_HANDOFF_WAIT = _T["client_handoff_wait"]
CLIENT_NEW_SEARCH_PROMPT = _T["client_new_search_prompt"]


def _criteria_summary(lead) -> str:
    parts = []
    if lead.districts:
        parts.append(f"район {', '.join(lead.districts)}")
    if lead.bedrooms:
        parts.append(f"{lead.bedrooms} спален")
    if lead.budget:
        parts.append(f"бюджет до {lead.budget:,.0f} THB/мес".replace(",", " "))
    if lead.check_in:
        parts.append(f"заезд {lead.check_in.strftime('%d.%m.%Y')}")
    return ", ".join(parts) if parts else "ваш запрос"


def client_new_search_prompt() -> str:
    return _T["client_new_search_prompt"]


def client_new_search_with_criteria(lead) -> str:
    return _T["client_new_search_with_criteria"].format(
        criteria_summary=_criteria_summary(lead),
    )


def _fmt_with_id(key: str, object_id: str, **kwargs) -> str:
    """Подстановка метки объекта: без ID убираем лишние пробелы аккуратно."""
    text = _T[key].format(object_id=object_id or "", **kwargs)
    return text.replace("  ", " ").replace(" .", ".").replace(" ,", ",")


def client_no_alternatives(object_id: str) -> str:
    return _fmt_with_id("client_no_alternatives", object_id)


def client_wait_owner(object_id: str) -> str:
    return _fmt_with_id("client_wait_owner", object_id)


def client_waiting_owner(object_id: str) -> str:
    return _fmt_with_id("client_waiting_owner", object_id)


def client_booking_confirmed(object_id: str, date_range: str) -> str:
    return _fmt_with_id("client_booking_confirmed", object_id, date_range=date_range)


def client_owner_confirmed(object_id: str, date_range: str) -> str:
    return _fmt_with_id("client_owner_confirmed", object_id, date_range=date_range)


def client_owner_conditions(object_id: str, note: str) -> str:
    return _fmt_with_id("client_owner_conditions", object_id, note=note)


def client_object_busy(object_id: str, busy_until: str, free_from: str) -> str:
    """Объект занят на даты клиента: сообщаем окно и предлагаем выбор."""
    return _fmt_with_id("client_object_busy", object_id,
                        busy_until=busy_until, free_from=free_from)


def client_object_partial(
    object_id: str,
    check_in: str,
    free_nights: int,
    free_until: str,
    busy_until: str,
    free_from: str,
) -> str:
    """Даты клиента открыты лишь частично: с желаемого заезда свободно
    только N ночей, дальше занято до даты X, полностью свободно с даты Y."""
    return _fmt_with_id(
        "client_object_partial", object_id,
        check_in=check_in,
        free_nights=free_nights,
        free_until=free_until,
        busy_until=busy_until,
        free_from=free_from,
    )


def client_offer_line(listing: Listing, reason: str = "", check_in=None) -> str:
    """Одна строка оффера: почему подходит + цена/район + ссылки (TG-пост, фото R2).

    check_in (date) — месяц заезда клиента: цена берётся из monthly_prices,
    prorated озвучивается как ориентировочная.
    """
    parts = [listing.title or listing.object_id]
    if listing.district:
        parts.append(f"район {listing.district}")
    quote = listing.price_quote(check_in)
    if quote:
        parts.append(quote)
    if reason:
        parts.append(reason)
    line = " — ".join(parts)
    links = [u for u in (listing.tg_post_url, listing.photos_url) if u]
    if links:
        line += "\n" + "\n".join(links)
    return line


# ---------- Собственник (Agent 8) ----------

def _load_agency() -> dict:
    """Агентство из общего config/project.json (корень монорепо)."""
    path = Path(__file__).resolve().parents[3] / "config" / "project.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("agency") or {}
    except (OSError, ValueError):
        return {}


AGENCY = _load_agency()
AGENCY_BRAND = AGENCY.get("brand", "OpenHome")


def owner_first_message(
    channel: OwnerChannel,
    check_in: str,
    check_out: str,
    guests: int | None = None,
    listing_url: str = "",
) -> str:
    """Первое сообщение владельцу по каналу.

    listing_url — ссылка на его объявление (Airbnb/FB), вставляется после
    «на ваше жильё», чтобы владелец сразу понял, о каком объекте речь.
    Дата выезда не указана = годовой контракт — так и говорим владельцу.
    WA / TG / FB Marketplace — сразу про клиента. Бюджет клиента владельцу
    НЕ сообщаем (это наша внутренняя информация для торга).
    Airbnb — НЕ про клиента и без запроса контакта (см. OWNER_AIRBNB_STEP2).
    """
    if channel == OwnerChannel.AIRBNB:
        return _T["owner_first_airbnb"]

    listing_ref = f" {listing_url.strip()}" if listing_url.strip() else ""
    key = "owner_first_base" if check_out.strip() else "owner_first_base_year"
    msg = _T[key].format(
        agency=AGENCY_BRAND, listing_ref=listing_ref,
        check_in=check_in, check_out=check_out,
    )
    if guests:
        msg += _T["owner_first_guests"].format(guests=guests)
    msg += _T["owner_first_ask"]
    if channel == OwnerChannel.FB_MARKETPLACE:
        msg += _T["owner_first_fb_whatsapp"]
    return msg


OWNER_AIRBNB_STEP2 = _T["owner_airbnb_step2"]
OWNER_BUSY_FOLLOWUP = _T["owner_busy_followup"]
OWNER_ACK_FREE = _T["owner_ack_free"]
OWNER_ACK_CONDITIONS = _T["owner_ack_conditions"]
OWNER_WA_ASK_CALENDAR = _T["owner_wa_ask_calendar"]
