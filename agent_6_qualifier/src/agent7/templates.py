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
    "client_qualify_core": (
        "Подскажите, пожалуйста, на какие даты планируете заезд и выезд, "
        "и сколько человек будет проживать?"
    ),
    "client_qualify_followup": (
        "Я могу подобрать вам и другие варианты, если хотите. "
        "Для этого сообщите желаемый район и бюджет — "
        "проверю всё в базе и, возможно, найду для вас что-то получше."
    ),
    "client_ask_checkout": (
        "И уточните, пожалуйста, до какой даты планируете проживание?"
    ),
    "client_ask_alternatives": (
        "Хотите, подберу для вас ещё несколько похожих вариантов?"
    ),
    "client_ask_budget_tolerance": (
        "Пока в рамках бюджета вариантов мало. Подскажите, какая погрешность "
        "по бюджету для вас допустима — рассмотрим чуть шире?"
    ),
    "client_no_alternatives": (
        "Пока похожих вариантов по вашим критериям в базе нет — "
        "сосредоточусь на выбранном объекте. "
        "Как только владелец ответит по доступности, сразу напишу вам."
    ),
    "client_wait_owner": (
        "Хорошо! Уточняю у владельца доступность на ваши даты "
        "и сразу вернусь к вам с ответом."
    ),
    "client_waiting_owner": (
        "Я уже отправил запрос владельцу и жду ответа по вашему объекту. "
        "Как только узнаю — сразу напишу вам."
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
    "client_booking_confirmed": (
        "Бронь зафиксирована по «{title}» на {date_range}. "
        "Наш менеджер сейчас подключится к диалогу и согласует с вами "
        "время просмотра жилья вместе с владельцем."
    ),
    "client_handoff_wait": (
        "Наш менеджер уже видит вашу заявку и скоро напишет вам "
        "для назначения просмотра. Ожидайте, пожалуйста."
    ),
    "client_owner_confirmed": (
        "Отличные новости! Владелец подтвердил доступность «{title}» "
        "на ваши даты ({date_range}). Подтверждаете бронь?"
    ),
    "client_owner_conditions": (
        "Владелец по объекту «{title}» ответил, но условия немного изменились: {note}. "
        "Подходят ли вам такие условия?"
    ),
    "client_object_busy": (
        "К сожалению, «{title}» занят до {busy_until} и будет свободен с {free_from}. "
        "Подойдут ли вам такие даты? Если нет — подберу похожие варианты."
    ),
    "client_object_partial": (
        "По объекту «{title}»: с вашей желаемой даты заезда {check_in} "
        "свободно всего {free_nights} дн. — выезд не позднее {free_until}. "
        "Дальше занято до {busy_until}, а полностью свободные даты — с {free_from}. "
        "Как вам удобнее: короткое заселение с {check_in} на {free_nights} дн. "
        "или заезд с {free_from}? Могу также подобрать похожие варианты."
    ),
    # ---------- Собственник (Agent 8) ----------
    # Airbnb — НЕ про клиента и без запроса контакта (см. owner_airbnb_step2).
    "owner_first_airbnb": "Здравствуйте! Меня интересует ваше жильё. Можно посмотреть дом?",
    "owner_first_base": (
        "Здравствуйте! У меня есть клиент на ваше жильё, "
        "готов заселиться с {check_in} по {check_out}."
    ),
    "owner_first_guests": " Гостей: {guests}.",
    "owner_first_budget": " Бюджет: {budget}.",
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

CLIENT_QUALIFY_CORE = _T["client_qualify_core"]
CLIENT_QUALIFY_FOLLOWUP = _T["client_qualify_followup"]
CLIENT_ASK_CHECKOUT = _T["client_ask_checkout"]
CLIENT_ASK_ALTERNATIVES = _T["client_ask_alternatives"]
CLIENT_ASK_BUDGET_TOLERANCE = _T["client_ask_budget_tolerance"]
CLIENT_NO_ALTERNATIVES = _T["client_no_alternatives"]
CLIENT_WAIT_OWNER = _T["client_wait_owner"]
CLIENT_WAITING_OWNER = _T["client_waiting_owner"]
CLIENT_BOOKING_FIO = _T["client_booking_fio"]
CLIENT_BOOKING_CITIZENSHIP = _T["client_booking_citizenship"]
CLIENT_HANDOFF_WAIT = _T["client_handoff_wait"]


def client_booking_confirmed(title: str, date_range: str) -> str:
    return _T["client_booking_confirmed"].format(title=title, date_range=date_range)


def client_owner_confirmed(title: str, date_range: str) -> str:
    return _T["client_owner_confirmed"].format(title=title, date_range=date_range)


def client_owner_conditions(title: str, note: str) -> str:
    return _T["client_owner_conditions"].format(title=title, note=note)


def client_object_busy(title: str, busy_until: str, free_from: str) -> str:
    """Объект занят на даты клиента: сообщаем окно и предлагаем выбор."""
    return _T["client_object_busy"].format(
        title=title, busy_until=busy_until, free_from=free_from
    )


def client_object_partial(
    title: str,
    check_in: str,
    free_nights: int,
    free_until: str,
    busy_until: str,
    free_from: str,
) -> str:
    """Даты клиента открыты лишь частично: с желаемого заезда свободно
    только N ночей, дальше занято до даты X, полностью свободно с даты Y."""
    return _T["client_object_partial"].format(
        title=title,
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

def owner_first_message(
    channel: OwnerChannel,
    check_in: str,
    check_out: str,
    guests: int | None = None,
    budget: str = "",
) -> str:
    """Первое сообщение владельцу по каналу.

    WA / TG / FB Marketplace — сразу про клиента.
    Airbnb — НЕ про клиента и без запроса контакта (см. OWNER_AIRBNB_STEP2).
    """
    if channel == OwnerChannel.AIRBNB:
        return _T["owner_first_airbnb"]

    msg = _T["owner_first_base"].format(check_in=check_in, check_out=check_out)
    if guests:
        msg += _T["owner_first_guests"].format(guests=guests)
    if budget:
        msg += _T["owner_first_budget"].format(budget=budget)
    msg += _T["owner_first_ask"]
    if channel == OwnerChannel.FB_MARKETPLACE:
        msg += _T["owner_first_fb_whatsapp"]
    return msg


OWNER_AIRBNB_STEP2 = _T["owner_airbnb_step2"]
OWNER_BUSY_FOLLOWUP = _T["owner_busy_followup"]
OWNER_ACK_FREE = _T["owner_ack_free"]
OWNER_ACK_CONDITIONS = _T["owner_ack_conditions"]
OWNER_WA_ASK_CALENDAR = _T["owner_wa_ask_calendar"]
