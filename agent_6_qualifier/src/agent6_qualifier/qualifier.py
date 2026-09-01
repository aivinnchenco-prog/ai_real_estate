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
from .object_id import extract_object_ids, extract_tg_post, extract_utm_campaign
from .templates import (
    CLIENT_ASK_ALTERNATIVES,
    CLIENT_ASK_OBJECT_LINK,
    CLIENT_ASK_OBJECT_OR_SEARCH,
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
    owner_request_id: str = ""        # WA Agent7 request correlation id
    booking_confirmed: bool = False
    booking_intent: bool = False         # клиент согласился на бронь
    handoff_to_human: bool = False    # живой менеджер подключается к диалогу
    human_handoff_active: bool = False
    human_handoff_at: str = ""
    last_human_message_at: str = ""
    last_client_message_at: str = ""
    pending_owner_requests: list = field(default_factory=list)
    last_outbound_template_key: str = ""
    shown_object_ids: list = field(default_factory=list)
    rejected_object_ids: list = field(default_factory=list)
    # ---- Wave 3: qualification policy V2 (all default-safe for old sessions) ----
    slot_meta: dict = field(default_factory=dict)       # slot -> confidence/source
    hard_constraints: list = field(default_factory=list)
    soft_preferences: list = field(default_factory=list)
    object_reactions: list = field(default_factory=list)
    liked_object_ids: list = field(default_factory=list)
    positive_preferences: list = field(default_factory=list)
    negative_preferences: list = field(default_factory=list)
    contradictions: list = field(default_factory=list)
    pending_clarification_slot: str = ""
    misunderstanding_count: int = 0
    repair_mode: bool = False
    last_repair_reason: str = ""
    lead_temperature: str = ""
    history: list = field(default_factory=list)  # [{"role":"user"|"assistant","text":...}]
    amo_lead_id: int | None = None
    language: str = "ru"
    # Publication reference (Agent 4 mapping → Agent 6 resolver)
    source_platform: str = ""
    source_publication_id: str = ""
    source_publication_url: str = ""
    source_channel: str = ""
    resolution_confidence: str = ""


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
    r"порекомендуй(те)?|найд(и|ите)|ищ(у|ем)|покаж\w*\s+вариант|покажи(те)?|"
    r"какие\s+(есть\s+)?варианты|что\s+(у\s+вас\s+)?есть|"
    r"есть\s+(ли\s+)?(что|вариант)|по\s+запросу",
    re.IGNORECASE,
)
# Клиент говорит, что пришёл по конкретному объекту, но ID/ссылку ещё не прислал.
_ASKS_DEPOSIT_RE = re.compile(
    r"(?i)депозит|залог|\bdeposit\b",
)

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
    awaiting_client_response: bool = False  # исходящее ждёт ответа клиента (SLA)
    skip_polish: bool = False         # не отдавать Gemini — шаблон точный
    silent: bool = False
    template_key: str = ""


class Qualifier:
    """listing_source — абстракция над Notion, чтобы тестировать без сети."""

    def __init__(self, find_by_id, fetch_all, find_by_tg_post=None, publication_store=None):
        self._find_by_id = find_by_id
        self._fetch_all = fetch_all
        self._find_by_tg_post = find_by_tg_post
        self._publication_store = publication_store

    # ---------- главный вход ----------

    def handle_message(self, session: Session, message: str, update: dict) -> Turn:
        """update — факты, извлечённые Gemini (brain.extract_lead_update)."""
        from .session_ownership import should_bot_respond, touch_client_message
        from .intent import classify_intent
        from .qualification_policy import preprocess_turn
        from .search_context import reset_search_context

        before = {
            "check_in": session.lead.check_in.isoformat() if session.lead.check_in else None,
            "stay_months": session.lead.stay_months,
            "budget": session.lead.budget,
            "guests": session.lead.guests,
            "bedrooms": session.lead.bedrooms,
            "preferred_object_id": session.lead.preferred_object_id,
        }
        # Wave 3 policy layer: confidence, corrections, conflicts, reactions,
        # repair. Applies the extract itself so corrections stay narrow.
        policy = preprocess_turn(session, message, update)
        events: list[str] = list(policy.events)
        touch_client_message(session)

        if not should_bot_respond(session):
            return Turn(reply_draft="", events=events, silent=True, skip_polish=True)

        handoff_turn = self._repair_handoff_turn(session, policy, events, message)
        if handoff_turn is not None:
            return handoff_turn

        intent = policy.forced_intent
        if intent is None:
            if (
                session.awaiting_owner
                or session.pending_owner_requests
                or session.human_handoff_active
                or session.handoff_to_human
                or policy.repair.triggered
            ):
                intent = classify_intent(message, session, policy.effective_update)
            else:
                # No stale workflow state to protect, but an explicit «нужен
                # менеджер» / «искать новый» must still be honoured. Rules only:
                # the LLM fallback stays reserved for ambiguous live state.
                from .intent import classify_intent_deterministic

                intent = classify_intent_deterministic(
                    message, session, policy.effective_update
                )

        if intent in ("NEW_PROPERTY_SEARCH", "CHANGE_CRITERIA"):
            reset_search_context(session, intent, policy.effective_update)
            events.append(f"Новый поиск: {intent}")
            listing_early = self._resolve_listing(message)
            if listing_early is not None:
                session.chosen = listing_early
                session.lead.preferred_object_id = listing_early.object_id
                session.wants_selection = False
            if intent == "NEW_PROPERTY_SEARCH":
                from .progressive_templates import build_progressive_reply
                from .slot_planner import plan_qualification

                plan = plan_qualification(session, session.chosen)
                lead = session.lead
                has_criteria = (
                    lead.districts or lead.budget or lead.bedrooms
                    or lead.check_in or lead.stay_months
                    or policy.effective_update.get("districts")
                    or policy.effective_update.get("budget")
                )
                if has_criteria and not plan.mvc_ready:
                    draft = build_progressive_reply(lead, plan, message=message)
                    session.asked_core = True
                elif has_criteria and plan.mvc_ready:
                    from .templates import client_new_search_with_criteria
                    draft = client_new_search_with_criteria(lead)
                    session.asked_core = True
                else:
                    from .templates import client_new_search_prompt
                    draft = client_new_search_prompt()
                return Turn(
                    reply_draft=self._with_repair_prefix(session, policy, draft),
                    events=events,
                    skip_polish=True,
                    awaiting_client_response=True,
                )

        # Repair mode must not re-send the template that already failed (§13).
        if (intent == "CONTINUE_CURRENT_REQUEST"
                and not policy.repair.triggered
                and (session.awaiting_owner or session.pending_owner_requests)):
            from .templates import client_waiting_owner
            oid = self._oid(session)
            if not session.awaiting_owner and session.pending_owner_requests:
                oid = session.pending_owner_requests[-1].get("object_id", oid)
            return Turn(
                reply_draft=client_waiting_owner(oid),
                events=events + ["Статус запроса владельцу"],
                skip_polish=True,
                template_key="client_waiting_owner",
            )

        if intent == "HUMAN_REQUIRED":
            from .session_ownership import activate_human_handoff
            activate_human_handoff(session)
            return Turn(
                reply_draft="Передал ваш запрос менеджеру — скоро подключится к диалогу.",
                events=events + ["Клиент просит менеджера"],
                handoff_to_human=True,
                skip_polish=True,
            )

        # One narrow question when two values are both plausible (§5).
        if policy.needs_clarification:
            session.pending_clarification_slot = policy.clarification_slot
            events.append(f"Противоречие: {policy.clarification_slot}")
            return Turn(
                reply_draft=self._with_repair_prefix(
                    session, policy, policy.clarification
                ),
                events=events,
                skip_polish=True,
                awaiting_client_response=True,
                template_key="contradiction_clarify",
            )

        import logging

        logging.getLogger(__name__).info(
            "agent6_turn chat=%s update=%s before=%s after_check_in=%s stay=%s obj=%s",
            session.chat_id,
            sorted((update or {}).keys()),
            before,
            session.lead.check_in.isoformat() if session.lead.check_in else None,
            session.lead.stay_months,
            session.lead.preferred_object_id,
        )

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
                            skip_polish=True, awaiting_client_response=True)

        if session.awaiting_alt_consent:
            if skips_alternatives(message, session):
                # Клиент отказался от альтернатив — не переспрашиваем.
                session.awaiting_alt_consent = False
                from .templates import client_wait_owner
                return Turn(reply_draft=client_wait_owner(self._oid(session)),
                            events=events)
            if update.get("wants_alternatives") or is_consent(message):
                session.awaiting_alt_consent = False
                session.offered_alternatives = True
                return self._show_alternatives(session, events)

        if session.handoff_to_human and not session.human_handoff_active:
            from .templates import CLIENT_HANDOFF_WAIT
            return Turn(reply_draft=CLIENT_HANDOFF_WAIT, events=events, skip_polish=True)

        if session.owner_verdict == "free" and not session.booking_confirmed:
            chosen = session.chosen
            lead = session.lead
            from .templates import (
                CLIENT_BOOKING_CITIZENSHIP,
                CLIENT_BOOKING_FIO,
                CLIENT_BOOKING_GUESTS,
                CLIENT_BOOKING_WHATSAPP,
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
            # Запасной разбор номера телефона без LLM (ждём его после гражданства).
            if not lead.whatsapp and lead.citizenship:
                m = re.search(r"\+?\d[\d\s\-()]{7,17}\d", message)
                if m:
                    lead.whatsapp = re.sub(r"[\s\-()]", "", m.group(0))
            if is_consent(message) or lead.full_name or lead.citizenship:
                session.booking_intent = True
            # Запасной разбор числа гостей без LLM (ждём его первым вопросом
            # после согласия на бронь; ФИО ещё не спрашивали).
            if (session.booking_intent and not lead.guests and not lead.full_name):
                m = re.search(r"\b(\d{1,2})\b", message)
                if m and 1 <= int(m.group(1)) <= 20:
                    lead.guests = int(m.group(1))
            if not session.booking_intent:
                return Turn(
                    reply_draft="Подтверждаете бронь на эти даты?",
                    events=events,
                    skip_polish=True,
                    awaiting_client_response=True,
                )
            if not lead.guests:
                return Turn(reply_draft=CLIENT_BOOKING_GUESTS, events=events,
                            skip_polish=True, awaiting_client_response=True)
            if not lead.full_name:
                return Turn(reply_draft=CLIENT_BOOKING_FIO, events=events,
                            skip_polish=True, awaiting_client_response=True)
            if not lead.citizenship:
                return Turn(reply_draft=CLIENT_BOOKING_CITIZENSHIP, events=events,
                            skip_polish=True, awaiting_client_response=True)
            if not lead.whatsapp:
                return Turn(reply_draft=CLIENT_BOOKING_WHATSAPP, events=events,
                            skip_polish=True, awaiting_client_response=True)
            session.booking_confirmed = True
            session.handoff_to_human = True
            dr = self._date_range(lead)
            ev = (
                f"Бронь подтверждена: {lead.full_name}, "
                f"гражданство {lead.citizenship}, WhatsApp {lead.whatsapp}, "
                f"гостей: {lead.guests} — нужен менеджер для просмотра"
            )
            return Turn(
                reply_draft=client_booking_confirmed(self._oid(session), dr),
                events=events + [ev],
                booking_confirmed=True,
                handoff_to_human=True,
                skip_polish=True,
            )

        reaction_turn = self._reaction_turn(session, policy, events, message)
        if reaction_turn is not None:
            return reaction_turn

        turn = self._next_step(session, events, message, policy=policy)
        return self._with_deposit_answer(session, message, update, turn)

    # ---------- Wave 3 branches ----------

    def _repair_handoff_turn(
        self, session: Session, policy, events: list[str], message: str,
    ) -> Turn | None:
        """Escalate only after AGENT6_MAX_REPAIR_ATTEMPTS failed repairs (§14)."""
        if not policy.repair.should_handoff:
            return None
        from .session_ownership import activate_human_handoff

        activate_human_handoff(session)
        events.append(
            "Порог непониманий превышен "
            f"({policy.repair.misunderstanding_count}) — передаю менеджеру"
        )
        return Turn(
            reply_draft=(
                "Подключаю менеджера — он разберётся в вашем запросе "
                "и ответит здесь же."
            ),
            events=events,
            handoff_to_human=True,
            skip_polish=True,
            template_key="repair_handoff",
        )

    @staticmethod
    def _with_repair_prefix(session: Session, policy, draft: str) -> str:
        """Short recap of the new understanding, no long apology (§13)."""
        if not policy.repair.triggered:
            return draft
        from .qualification_policy import build_repair_understanding

        recap = build_repair_understanding(session)
        if not recap or recap in draft:
            return draft
        return f"{recap}\n\n{draft}"

    def _reaction_turn(
        self, session: Session, policy, events: list[str], message: str,
    ) -> Turn | None:
        """Acknowledge an object reaction and move to the next useful action."""
        from .qualification_policy import (
            build_reaction_reply,
            reaction_owns_turn,
            reaction_refine_question,
        )

        if not reaction_owns_turn(policy):
            return None

        draft, extra_events = build_reaction_reply(session, policy)
        events.extend(extra_events)

        from .reactions import ReactionType
        if (
            policy.reaction is not None
            and policy.reaction.reaction_type == ReactionType.TOO_CHEAP
        ):
            return Turn(
                reply_draft=self._with_repair_prefix(session, policy, draft),
                events=events,
                skip_polish=True,
                awaiting_client_response=True,
                template_key="reaction_too_cheap",
            )

        question = reaction_refine_question(session, policy)
        if question:
            return Turn(
                reply_draft=self._with_repair_prefix(
                    session, policy, f"{draft}\n\n{question}"
                ),
                events=events,
                skip_polish=True,
                awaiting_client_response=True,
                template_key="reaction_refine",
            )

        alt_turn = self._show_alternatives(session, events)
        session.offered_alternatives = True
        combined = f"{draft}\n\n{alt_turn.reply_draft}".strip()
        return Turn(
            reply_draft=self._with_repair_prefix(session, policy, combined),
            events=alt_turn.events,
            skip_polish=alt_turn.skip_polish,
            awaiting_client_response=True,
            template_key="reaction_refine",
        )

    # ---------- решение следующего шага ----------

    def _next_step(
        self,
        session: Session,
        events: list[str],
        message: str = "",
        *,
        policy=None,
    ) -> Turn:
        lead, chosen = session.lead, session.chosen
        parts: list[str] = []
        in_repair = bool(policy is not None and policy.repair.triggered)

        if session.awaiting_owner and not session.owner_verdict and not in_repair:
            from .templates import client_waiting_owner
            key = "client_waiting_owner"
            if session.last_outbound_template_key == key:
                from .intent import classify_intent as _reclassify
                forced = _reclassify(message, session, {}, use_llm=False)
                if forced in ("NEW_PROPERTY_SEARCH", "CHANGE_CRITERIA"):
                    from .search_context import reset_search_context
                    reset_search_context(session, forced, {})
                    events.append(f"Принудительный новый поиск: {forced}")
                    from .templates import client_new_search_prompt
                    return Turn(
                        reply_draft=client_new_search_prompt(),
                        events=events,
                        skip_polish=True,
                        awaiting_client_response=True,
                    )
            session.last_outbound_template_key = key
            return Turn(
                reply_draft=client_waiting_owner(self._oid(session)),
                events=events,
                skip_polish=True,
                template_key=key,
                awaiting_client_response=True,
            )

        # Клиент пришёл без объекта, без запроса на подбор и без параметров —
        # выясняем: конкретный объект с наших ресурсов или подбор по запросу.
        if (chosen is None and not session.wants_selection
                and not session.asked_object_source
                and not (lead.check_in or lead.guests or lead.budget
                         or lead.districts or lead.stay_months)):
            session.asked_object_source = True
            events.append("Спросили: конкретный объект или подбор")
            return Turn(reply_draft=CLIENT_ASK_OBJECT_OR_SEARCH, events=events,
                        skip_polish=True, awaiting_client_response=True)

        if chosen is None and session.wants_selection:
            has_criteria = (
                lead.districts or lead.budget or lead.bedrooms or lead.check_in
                or lead.guests or lead.stay_months
            )
            wants_show = bool(_WANTS_SELECTION_RE.search(message or ""))
            if has_criteria or session.pending_owner_requests:
                from .slot_planner import plan_qualification

                plan = plan_qualification(session, chosen)
                if plan.mvc_ready or (
                    session.pending_owner_requests and wants_show and has_criteria
                ):
                    session.offered_alternatives = True
                    session.asked_core = True
                    return self._show_alternatives(session, events)

        # Facebook / rental policy before progressive questions.
        if chosen is not None and lead.check_in:
            from .rental_policy import evaluate_rental_policy

            policy = evaluate_rental_policy(chosen, lead)
            if policy.needs_duration_clarification:
                events.append(f"rental_policy:{policy.reason}")
                return Turn(
                    reply_draft=policy.prompt,
                    events=events,
                    skip_polish=True,
                    awaiting_client_response=True,
                )

        from .progressive_templates import build_progressive_reply
        from .slot_planner import plan_qualification

        plan = plan_qualification(session, chosen)
        if not plan.mvc_ready:
            session.asked_core = True
            session.asked_followup = True
            session.asked_checkout = True
            prefix = ""
            if chosen is not None and not session.price_quoted:
                prefix = self._confirm_object_line(chosen)
            draft = build_progressive_reply(
                lead, plan, message=message, prefix=prefix,
            )
            if policy is not None:
                draft = self._with_repair_prefix(session, policy, draft)
            events.append(f"Прогрессивная квалификация: {plan.next_slots[:2]}")
            return Turn(
                reply_draft=draft,
                events=events,
                skip_polish=True,
                awaiting_client_response=True,
            )

        if chosen is None and session.wants_selection:
            session.offered_alternatives = True
            session.asked_core = True
            return self._show_alternatives(session, events)

        # Ориентировочная цена (один раз): период короче месяца — пропорцией
        # от месячной цены, иначе месячная из monthly_prices.
        if chosen is not None and not session.price_quoted:
            quote = chosen.price_quote_for_period(lead.check_in, lead.check_out)
            if quote:
                parts.append(client_price_line(quote))
            session.price_quoted = True

        # Критерии получены -> отправляем ссылки на пост, фото и карту (один раз).
        if chosen is not None and not session.links_sent:
            links = []
            if chosen.tg_post_url:
                links.append(f"Пост с описанием: {chosen.tg_post_url}")
            if chosen.photos_url:
                links.append(f"Все фото: {chosen.photos_url}")
            if chosen.google_maps:
                links.append(f"Локация на карте: {chosen.google_maps}")
            if links:
                parts.append("\n".join(links))
                session.links_sent = True

        # Выбранный объект занят на даты клиента -> окно доступности + выбор.
        if chosen is not None and self._busy_for_dates(chosen, lead):
            busy_until = chosen.busy_until.strftime("%d.%m.%Y")
            free_from = self._free_from(chosen)
            session.awaiting_alt_consent = True
            parts.append(client_object_busy(chosen.object_id, busy_until, free_from))
            events.append(f"Объект {chosen.object_id} занят до {busy_until}")
            return Turn(reply_draft="\n\n".join(parts), events=events,
                        awaiting_client_response=True)

        # Квалификация собрана, владелец ещё НЕ отвечал -> запрос Agent 8.
        if (chosen is not None and not session.awaiting_alt_consent
                and not session.owner_verdict and not session.awaiting_owner):
            parts.append(
                f"Отлично, передаю запрос по вашему варианту {chosen.object_id} — "
                "уточню у владельца доступность на ваши даты и сразу вернусь "
                "с ответом."
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
        from .matching import find_alternatives_for_session

        listings = self._fetch_all()
        candidates = find_alternatives_for_session(listings, session)
        if not candidates:
            # Shortlist exhausted: a previously rejected object may come back,
            # but only with an explicit explanation (policy §10).
            fallback = find_alternatives_for_session(
                listings, session, allow_rejected_fallback=True,
            )
            if fallback:
                lines = [
                    "Других новых вариантов под ваши критерии сейчас нет. "
                    "Могу вернуться к варианту, который вы уже смотрели:"
                ]
                for l in fallback:
                    lines.append(client_offer_line(l, check_in=session.lead.check_in))
                    events.append(f"Повторно предложен отклонённый {l.object_id}")
                lines.append(
                    "Если он точно не подходит — расширим критерии "
                    "(бюджет или район), и я поищу дальше."
                )
                return Turn(reply_draft="\n\n".join(lines), events=events,
                            awaiting_client_response=True)
        if not candidates:
            # Клиент уже ждёт ответа владельца по выбранному объекту:
            # просить расширить бюджет бессмысленно — честно говорим, что
            # альтернатив пока нет, и ждём вердикта владельца.
            if session.chosen is not None and session.awaiting_owner:
                from .templates import client_no_alternatives
                return Turn(reply_draft=client_no_alternatives(self._oid(session)),
                            events=events, skip_polish=True)
            from .matching import empty_shortlist_reason, find_alternatives
            from .reactions import rejected_ids
            from .templates import CLIENT_NO_NEW_ALTERNATIVES, client_empty_shortlist

            still_there = find_alternatives(
                listings,
                session.lead,
                chosen=session.chosen,
                exclude_ids=rejected_ids(session),
            )
            if still_there:
                return Turn(
                    reply_draft=CLIENT_NO_NEW_ALTERNATIVES,
                    events=events + ["Новых вариантов нет — показанные исключены"],
                    skip_polish=True,
                    awaiting_client_response=True,
                    template_key="client_no_new_alternatives",
                )
            reason = empty_shortlist_reason(listings, session.lead)
            return Turn(
                reply_draft=client_empty_shortlist(
                    reason, budget_set=session.lead.budget is not None,
                ),
                events=events + [f"Пустая выдача: {reason}"],
                skip_polish=True,
                awaiting_client_response=True,
                template_key="client_empty_shortlist",
            )

        lines = ["Вот что могу предложить:"]
        for l in candidates:
            lines.append(client_offer_line(l, check_in=session.lead.check_in))
            events.append(f"Предложена альтернатива {l.object_id}")
            if l.object_id not in session.shown_object_ids:
                session.shown_object_ids.append(l.object_id)
        lines.append("Какой-то из вариантов интересен? Могу уточнить детали у владельца.")
        return Turn(
            reply_draft="\n\n".join(lines),
            events=events,
            skip_polish=True,
            template_key="_show_alternatives",
        )

    # ---------- вспомогательное ----------

    def _with_deposit_answer(
        self, session: Session, message: str, update: dict, turn: Turn,
    ) -> Turn:
        asked = bool((update or {}).get("asks_deposit")) or bool(
            _ASKS_DEPOSIT_RE.search(message or "")
        )
        if not asked or turn.silent:
            return turn
        from .templates import client_deposit_answer

        listing = session.chosen
        if listing is None:
            shown = list(getattr(session, "shown_object_ids", None) or [])
            if shown:
                listing = self._find_by_id(shown[-1])
        extra = client_deposit_answer(listing)
        draft = turn.reply_draft or ""
        if extra in draft:
            return turn
        turn.reply_draft = f"{extra}\n\n{draft}".strip()
        turn.events = list(turn.events or []) + ["Ответ по депозиту"]
        return turn

    @staticmethod
    def _oid(session: Session) -> str:
        """Метка объекта для сообщений клиенту («ваш вариант A_20260713_003»)."""
        if session.chosen is not None:
            return session.chosen.object_id
        return session.lead.preferred_object_id or ""

    def _resolve_listing(self, message: str) -> Listing | None:
        if self._publication_store is not None:
            from .publication_resolver import resolve_publication_reference

            resolution = resolve_publication_reference(
                message,
                None,
                None,
                None,
                None,
                store=self._publication_store,
            )
            if resolution.found and resolution.listing_id:
                listing = self._find_by_id(resolution.listing_id)
                if listing is not None:
                    return listing
            if resolution.confidence == "object_id_in_text" and resolution.listing_id:
                return self._find_by_id(resolution.listing_id)

        ids = extract_object_ids(message)
        if ids:
            return self._find_by_id(ids[0])
        utm_oid = extract_utm_campaign(message)
        if utm_oid:
            return self._find_by_id(utm_oid)
        tg = extract_tg_post(message)
        if tg and self._find_by_tg_post:
            return self._find_by_tg_post(*tg)
        return None

    @staticmethod
    def _confirm_object_line(listing: Listing) -> str:
        """«Да, это вилла, 3 спальни, район Чонг Тале» — факты из таблицы.
        Цену здесь не называем: она зависит от месяца заезда (monthly_prices)
        и озвучивается после того, как клиент назовёт даты."""
        from .morphology import bedrooms_phrase

        bits = []
        if listing.housing_type:
            bits.append(f"это {listing.housing_type.lower()}")
        if listing.rooms:
            bits.append(bedrooms_phrase(listing.rooms))
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
