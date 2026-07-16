"""Оркестратор диалога Agent 7: сообщение клиента → действия → черновик ответа.

Детерминированная логика (никакой свободы LLM в решениях):
1. Найти Объект ID / ссылку на TG-пост в сообщении → подтянуть объект из Notion.
2. Обновить профиль лида фактами из сообщения (извлекает Gemini).
3. Решить следующий шаг: подтвердить объект, добрать поля, спросить про
   альтернативы, показать альтернативы, сообщить «занято до X / свободно с Y».
4. Черновик ответа собирается из шаблонов, Gemini только полирует тон.

Каналы (TG userbot / WA) вызывают handle_message() и отправляют reply.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .matching import find_alternatives
from .models import Availability, LeadProfile, Listing
from .object_id import extract_object_ids, extract_tg_post
from .templates import (
    CLIENT_ASK_ALTERNATIVES,
    CLIENT_ASK_DATES,
    CLIENT_ASK_OBJECT_LINK,
    CLIENT_ASK_OBJECT_OR_SEARCH,
    CLIENT_QUALIFY_BULLETS,
    client_object_busy,
    client_offer_line,
    client_price_line,
)


@dataclass
class Session:
    """Состояние диалога с одним клиентом (ключ — chat_id канала)."""
    chat_id: str
    lead: LeadProfile = field(default_factory=LeadProfile)
    chosen: Listing | None = None
    asked_object_source: bool = False  # спросили «конкретный объект или подбор?»
    wants_selection: bool = False      # клиент просит подбор по запросу
    asked_core: bool = False          # анкета-критерии уже отправлена
    asked_followup: bool = False      # (легаси, оставлено для старых сессий)
    asked_checkout: bool = False      # (легаси, оставлено для старых сессий)
    price_quoted: bool = False        # ориентировочную цену уже назвали
    offered_alternatives: bool = False
    awaiting_alt_consent: bool = False
    links_sent: bool = False          # ссылки на TG-пост и фото уже отправлены
    only_chosen: bool = False         # клиент хочет только выбранный объект
    awaiting_owner: bool = False      # запрос отправлен владельцу, ждём ответ
    owner_verdict: str = ""           # free | busy | conditions
    booking_confirmed: bool = False
    booking_intent: bool = False         # клиент согласился на бронь
    handoff_to_human: bool = False    # живой менеджер подключается к диалогу
    history: list = field(default_factory=list)  # [{"role":"user"|"assistant","text":...}]
    amo_lead_id: int | None = None
    language: str = "ru"


# Короткие согласие/отказ распознаём без LLM: у Gemini нет истории диалога,
# и односложные ответы он трактует ненадёжно. Отказ проверяется ПЕРВЫМ,
# т.к. «не хочу» содержит слово «хочу» и иначе сойдёт за согласие.
_CONSENT_RE = re.compile(
    r"\b(да|хочу|давай(те)?|покажи(те)?|конечно|ага|ок|окей|можно|yes|sure|ok)\b",
    re.IGNORECASE,
)
_DECLINE_RE = re.compile(
    r"\b(нет|не надо|не нужно|не хочу|не стоит|только этот|только его|no|nope)\b",
    re.IGNORECASE,
)
# Клиент просит подбор («подберите», «ищу», «какие есть варианты») —
# без объекта сразу идём в квалификацию, не переспрашивая про конкретный объект.
_WANTS_SELECTION_RE = re.compile(
    r"подбор|подобрать|подбер(и|ите)|посоветуй(те)?|предложи(те)?|"
    r"порекомендуй(те)?|найд(и|ите)|ищ(у|ем)|"
    r"какие\s+(есть\s+)?варианты|что\s+(у\s+вас\s+)?есть|"
    r"есть\s+(ли\s+)?(что|вариант)|по\s+запросу",
    re.IGNORECASE,
)
# Клиент говорит, что пришёл по конкретному объекту, но ID/ссылку ещё не прислал.
_HAS_SPECIFIC_RE = re.compile(
    r"конкретн\w+|(у?видел\w*|смотрел\w*|нашел|нашёл)\b|"
    r"(ваш\w*|на\s+вашем)\s+(пост|канал|сайт|страниц|инстаграм|объявлен)",
    re.IGNORECASE,
)
# Клиент доволен выбранным объектом — альтернативы не предлагаем.
_PREFERS_CHOSEN_RE = re.compile(
    r"(мне\s+)?нравится\s+этот|"
    r"только\s+(этот|его|эту|это)|"
    r"интересует\s+только|"
    r"меня\s+(устраивает|устроит)\s+этот|"
    r"этот\s+(вариант\s+)?(подходит|устраивает)|"
    r"(беру|оставляем)\s+этот|"
    r"хочу\s+именно\s+этот|"
    r"другие\s+не\s+нужн",
    re.IGNORECASE,
)


def is_consent(message: str) -> bool:
    if prefers_chosen_only(message):
        return False
    return bool(_CONSENT_RE.search(message or ""))


def is_decline(message: str) -> bool:
    return bool(_DECLINE_RE.search(message or ""))


def prefers_chosen_only(message: str) -> bool:
    """«Мне нравится этот вариант» и похожие — без вопроса про альтернативы."""
    return bool(_PREFERS_CHOSEN_RE.search(message or ""))


def skips_alternatives(message: str, session: Session) -> bool:
    return session.only_chosen or is_decline(message) or prefers_chosen_only(message)


@dataclass
class Turn:
    """Результат обработки одного сообщения."""
    reply_draft: str                  # черновик (до полировки Gemini)
    events: list[str] = field(default_factory=list)   # для CRM-примечаний и логов
    need_owner_check: bool = False    # пора отдавать объект Agent 8
    booking_confirmed: bool = False   # клиент подтвердил бронь
    handoff_to_human: bool = False    # передать живому менеджеру
    skip_polish: bool = False         # не отдавать Gemini — шаблон точный


class Qualifier:
    """listing_source — абстракция над Notion, чтобы тестировать без сети."""

    def __init__(self, find_by_id, fetch_all, find_by_tg_post=None):
        self._find_by_id = find_by_id
        self._fetch_all = fetch_all
        self._find_by_tg_post = find_by_tg_post

    # ---------- главный вход ----------

    def handle_message(self, session: Session, message: str, update: dict) -> Turn:
        """update — факты, извлечённые Gemini (brain.extract_lead_update)."""
        from .brain import apply_update
        apply_update(session.lead, update)
        events: list[str] = []

        # Владелец уже ответил — не держим флаг «ждём владельца».
        if session.owner_verdict:
            session.awaiting_owner = False

        listing = self._resolve_listing(message)
        if listing is not None:
            session.chosen = listing
            session.lead.preferred_object_id = listing.object_id
            events.append(f"Клиент указал объект {listing.object_id}")
        elif session.chosen is None:
            # Номер в сообщении был, но в базе не нашёлся (опечатка/старый пост) —
            # говорим честно, а не задаём вопрос «конкретный или подбор».
            missing = extract_object_ids(message)
            if missing:
                from .templates import client_object_not_found
                events.append(f"Объект {missing[0]} не найден в базе")
                return Turn(reply_draft=client_object_not_found(missing[0]),
                            events=events, skip_polish=True)

        if update.get("language"):
            session.language = update["language"]
        # «Интересует объект X» — просто выбор объекта, не отказ от альтернатив:
        # флагу Gemini в сообщении с ID верим только при явной фразе «только этот».
        if listing is None and (update.get("prefers_chosen_only")
                                or update.get("declines_alternatives")):
            session.only_chosen = True
        if prefers_chosen_only(message) or is_decline(message):
            session.only_chosen = True

        # Клиент без объекта: «подберите/ищу/какие варианты» — это запрос подбора.
        if session.chosen is None and _WANTS_SELECTION_RE.search(message or ""):
            session.wants_selection = True
        # Ответ на вопрос «конкретный объект или подбор?»
        if (session.asked_object_source and session.chosen is None
                and not session.wants_selection):
            if update.get("wants_alternatives"):
                session.wants_selection = True
            elif _HAS_SPECIFIC_RE.search(message or ""):
                # Пришёл по конкретному объекту, но ID/ссылки нет — просим прислать.
                return Turn(reply_draft=CLIENT_ASK_OBJECT_LINK, events=events,
                            skip_polish=True)

        if session.awaiting_alt_consent:
            if skips_alternatives(message, session):
                # Клиент отказался от альтернатив — не переспрашиваем.
                session.awaiting_alt_consent = False
                from .templates import CLIENT_WAIT_OWNER
                return Turn(reply_draft=CLIENT_WAIT_OWNER, events=events)
            if update.get("wants_alternatives") or is_consent(message):
                session.awaiting_alt_consent = False
                session.offered_alternatives = True
                return self._show_alternatives(session, events)

        # Владелец подтвердил — бронь: согласие → ФИО + гражданство → передача менеджеру.
        if session.handoff_to_human:
            from .templates import CLIENT_HANDOFF_WAIT
            return Turn(reply_draft=CLIENT_HANDOFF_WAIT, events=events, skip_polish=True)

        if session.owner_verdict == "free" and not session.booking_confirmed:
            chosen = session.chosen
            lead = session.lead
            from .templates import (
                CLIENT_BOOKING_CITIZENSHIP,
                CLIENT_BOOKING_FIO,
                client_booking_confirmed,
            )
            if is_decline(message) and not session.booking_intent:
                session.owner_verdict = ""
                return Turn(
                    reply_draft="Понял вас. Если передумаете или нужны другие даты — напишите.",
                    events=events + ["Клиент отказался от брони"],
                )
            # Запасной разбор «гражданство X» без LLM: Gemini иногда теряет
            # гражданство, когда оно прислано одним сообщением с ФИО.
            if not lead.citizenship:
                m = re.search(
                    r"гражданств\w*[\s:—\-]*([А-ЯЁA-Zа-яёa-z][а-яёa-zА-ЯЁA-Z\- ]{2,30})",
                    message,
                    re.IGNORECASE,
                )
                if m:
                    lead.citizenship = m.group(1).strip(" .,!")
            if is_consent(message) or lead.full_name or lead.citizenship:
                session.booking_intent = True
            if not session.booking_intent:
                return Turn(
                    reply_draft="Подтверждаете бронь на эти даты?",
                    events=events,
                    skip_polish=True,
                )
            if not lead.full_name:
                return Turn(reply_draft=CLIENT_BOOKING_FIO, events=events, skip_polish=True)
            if not lead.citizenship:
                return Turn(reply_draft=CLIENT_BOOKING_CITIZENSHIP, events=events, skip_polish=True)
            session.booking_confirmed = True
            session.handoff_to_human = True
            title = (chosen.title if chosen else lead.preferred_object_id)
            dr = self._date_range(lead)
            ev = (
                f"Бронь подтверждена: {lead.full_name}, "
                f"гражданство {lead.citizenship} — нужен менеджер для просмотра"
            )
            return Turn(
                reply_draft=client_booking_confirmed(title, dr),
                events=events + [ev],
                booking_confirmed=True,
                handoff_to_human=True,
                skip_polish=True,
            )

        return self._next_step(session, events, message)

    # ---------- решение следующего шага ----------

    def _next_step(self, session: Session, events: list[str], message: str = "") -> Turn:
        lead, chosen = session.lead, session.chosen
        parts: list[str] = []

        if session.awaiting_owner and not session.owner_verdict:
            from .templates import CLIENT_WAITING_OWNER
            return Turn(reply_draft=CLIENT_WAITING_OWNER, events=events, skip_polish=True)

        # Клиент пришёл без объекта, без запроса на подбор и без параметров —
        # выясняем: конкретный объект с наших ресурсов или подбор по запросу.
        if (chosen is None and not session.wants_selection
                and not session.asked_object_source
                and not (lead.check_in or lead.guests or lead.budget
                         or lead.districts or lead.stay_months)):
            session.asked_object_source = True
            events.append("Спросили: конкретный объект или подбор")
            return Turn(reply_draft=CLIENT_ASK_OBJECT_OR_SEARCH, events=events,
                        skip_polish=True)

        # Анкета-критерии одним сообщением (+ подтверждение объекта из таблицы).
        # Дата выезда не указана = контракт на год, переспрашивать не нужно.
        if not session.asked_core:
            session.asked_core = True
            session.asked_followup = True
            session.asked_checkout = True
            if chosen is not None:
                parts.append(self._confirm_object_line(chosen))
            parts.append(CLIENT_QUALIFY_BULLETS)
            return Turn(reply_draft="\n\n".join(parts), events=events, skip_polish=True)

        # Минимум для проверки доступности и цены — дата заезда.
        if not lead.check_in:
            parts.append(CLIENT_ASK_DATES)
            return Turn(reply_draft="\n\n".join(parts), events=events, skip_polish=True)

        # Ориентировочная цена (один раз): период короче месяца — пропорцией
        # от месячной цены, иначе месячная из monthly_prices.
        if chosen is not None and not session.price_quoted:
            quote = chosen.price_quote_for_period(lead.check_in, lead.check_out)
            if quote:
                parts.append(client_price_line(quote))
            session.price_quoted = True

        # Критерии получены -> отправляем ссылки на пост и фото (один раз).
        if chosen is not None and not session.links_sent:
            links = []
            if chosen.tg_post_url:
                links.append(f"Пост с описанием: {chosen.tg_post_url}")
            if chosen.photos_url:
                links.append(f"Все фото: {chosen.photos_url}")
            if links:
                parts.append("\n".join(links))
                session.links_sent = True

        # Выбранный объект занят на даты клиента -> окно доступности + выбор.
        if chosen is not None and self._busy_for_dates(chosen, lead):
            busy_until = chosen.busy_until.strftime("%d.%m.%Y")
            free_from = self._free_from(chosen)
            session.awaiting_alt_consent = True
            parts.append(client_object_busy(chosen.title or chosen.object_id,
                                            busy_until, free_from))
            events.append(f"Объект {chosen.object_id} занят до {busy_until}")
            return Turn(reply_draft="\n\n".join(parts), events=events)

        # Квалификация собрана, владелец ещё НЕ отвечал -> запрос Agent 8.
        if (chosen is not None and not session.awaiting_alt_consent
                and not session.owner_verdict and not session.awaiting_owner):
            parts.append(
                "Отлично, передаю запрос по этому объекту — уточню у владельца "
                "доступность на ваши даты и сразу вернусь с ответом."
            )
            # Клиент выбрал только этот объект — альтернативы не навязываем.
            if not skips_alternatives(message, session):
                session.awaiting_alt_consent = True
                parts.append(CLIENT_ASK_ALTERNATIVES)
            else:
                session.only_chosen = True
            events.append(f"Запрос владельцу по {chosen.object_id}")
            session.awaiting_owner = True
            return Turn(reply_draft="\n\n".join(parts), events=events,
                        need_owner_check=True)

        # Объект не выбран, квалификация есть -> сразу подбор.
        if chosen is None:
            session.offered_alternatives = True
            return self._show_alternatives(session, events)

        if not session.only_chosen:
            parts.append(CLIENT_ASK_ALTERNATIVES)
            session.awaiting_alt_consent = True
        return Turn(reply_draft="\n\n".join(parts), events=events)

    # ---------- альтернативы ----------

    def _show_alternatives(self, session: Session, events: list[str]) -> Turn:
        candidates = find_alternatives(
            self._fetch_all(), session.lead, chosen=session.chosen,
        )
        if not candidates:
            # Клиент уже ждёт ответа владельца по выбранному объекту:
            # просить расширить бюджет бессмысленно — честно говорим, что
            # альтернатив пока нет, и ждём вердикта владельца.
            if session.chosen is not None and session.awaiting_owner:
                from .templates import CLIENT_NO_ALTERNATIVES
                return Turn(reply_draft=CLIENT_NO_ALTERNATIVES, events=events,
                            skip_polish=True)
            from .templates import CLIENT_ASK_BUDGET_TOLERANCE
            return Turn(reply_draft=CLIENT_ASK_BUDGET_TOLERANCE, events=events)

        lines = ["Вот что могу предложить:"]
        for l in candidates:
            lines.append(client_offer_line(l, check_in=session.lead.check_in))
            events.append(f"Предложена альтернатива {l.object_id}")
        lines.append("Какой-то из вариантов интересен? Могу уточнить детали у владельца.")
        return Turn(reply_draft="\n\n".join(lines), events=events)

    # ---------- вспомогательное ----------

    def _resolve_listing(self, message: str) -> Listing | None:
        ids = extract_object_ids(message)
        if ids:
            return self._find_by_id(ids[0])
        tg = extract_tg_post(message)
        if tg and self._find_by_tg_post:
            return self._find_by_tg_post(*tg)
        return None

    @staticmethod
    def _confirm_object_line(listing: Listing) -> str:
        """«Да, это вилла, 3 спальни, район Чонг Тале» — факты из таблицы.
        Цену здесь не называем: она зависит от месяца заезда (monthly_prices)
        и озвучивается после того, как клиент назовёт даты."""
        bits = []
        if listing.housing_type:
            bits.append(f"это {listing.housing_type.lower()}")
        if listing.rooms:
            n = listing.rooms
            word = ("спальня" if n % 10 == 1 and n % 100 != 11
                    else "спальни" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14)
                    else "спален")
            bits.append(f"{n} {word}")
        if listing.district:
            bits.append(f"район {listing.district}")
        if not bits:
            return f"Да, объект «{listing.title or listing.object_id}» у нас в базе."
        return "Здравствуйте! Да, " + ", ".join(bits) + "."

    @staticmethod
    def _busy_for_dates(listing: Listing, lead: LeadProfile) -> bool:
        return (
            listing.availability == Availability.BUSY
            and listing.busy_until is not None
            and lead.check_in is not None
            and listing.busy_until >= lead.check_in
        )

    @staticmethod
    def _date_range(lead: LeadProfile) -> str:
        if lead.check_in and lead.check_out:
            return f"{lead.check_in.strftime('%d.%m')}–{lead.check_out.strftime('%d.%m.%Y')}"
        if lead.check_in:
            return lead.check_in.strftime("%d.%m.%Y")
        return "ваши даты"

    @staticmethod
    def _free_from(listing: Listing) -> str:
        from datetime import timedelta
        return (listing.busy_until + timedelta(days=1)).strftime("%d.%m.%Y")
