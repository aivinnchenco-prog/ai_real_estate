"""Conversation / owner strategy hints from knowledge (advisory only)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from agent6_qualifier.knowledge.models import RetrievalContext, RetrievalResult
from agent6_qualifier.knowledge.retriever import retrieve_for_turn
from agent6_qualifier.models import LeadProfile
from agent6_qualifier.qualifier import Session


@dataclass
class ConversationStrategy:
    current_stage: str = ""
    known_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    client_intent: str = ""
    object_context: str = ""
    rental_policy: str = ""
    recommended_action: str = ""
    do_not_do: list[str] = field(default_factory=list)
    knowledge_refs: list[str] = field(default_factory=list)
    response_goal: str = ""
    knowledge_compact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OwnerStrategy:
    object_id: str = ""
    source: str = ""
    rental_policy: str = ""
    requested_dates: str = ""
    duration: str = ""
    verification_needed: list[str] = field(default_factory=list)
    questions_for_owner: list[str] = field(default_factory=list)
    knowledge_refs: list[str] = field(default_factory=list)
    do_not_do: list[str] = field(default_factory=list)
    knowledge_compact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _known_missing(lead: LeadProfile) -> tuple[list[str], list[str]]:
    known: list[str] = []
    missing: list[str] = []
    mapping = [
        ("check_in", lead.check_in),
        ("stay_months", lead.stay_months),
        ("budget", lead.budget),
        ("guests", lead.guests),
        ("bedrooms", lead.bedrooms),
        ("districts", lead.districts),
    ]
    for name, val in mapping:
        if val not in (None, "", [], {}):
            known.append(name)
        else:
            # districts empty list = missing for matching, but not always mandatory for owner check
            if name == "districts" and not val:
                missing.append(name)
            elif name != "districts" and val in (None, ""):
                missing.append(name)
            elif name in ("stay_months", "budget", "guests", "bedrooms") and val is None:
                missing.append(name)
    # check_in / guests / budget / districts are matching core (models.missing_core_fields)
    for m in lead.missing_core_fields():
        key = {
            "даты заезда": "check_in",
            "количество человек": "guests",
            "бюджет": "budget",
            "район": "districts",
        }.get(m, m)
        if key not in missing and key not in known:
            missing.append(key)
    return known, missing


def build_conversation_strategy(
    session: Session,
    *,
    message: str = "",
    intent: str = "",
    object_source: str = "",
    rental_policy: str = "",
    objection: str = "",
    situation: str = "",
) -> ConversationStrategy:
    from agent6_qualifier.context import dialog_stage
    from agent6_qualifier.rental_policy import evaluate_rental_policy, listing_source_kind

    lead = session.lead
    known, missing = _known_missing(lead)
    src = object_source or listing_source_kind(session.chosen)
    policy = rental_policy
    if not policy and session.chosen is not None:
        policy = evaluate_rental_policy(session.chosen, lead).policy

    intent_u = intent or (
        "specific_property"
        if (session.chosen or lead.preferred_object_id)
        else "generic_inquiry"
    )
    long_term = None
    if lead.stay_months is not None:
        long_term = float(lead.stay_months) >= 6.0

    ctx = RetrievalContext(
        agent="AGENT6",
        stage=dialog_stage(session),
        intent=intent_u,
        object_source=src,
        rental_policy=policy,
        known_fields=known,
        missing_fields=missing,
        current_object=lead.preferred_object_id or "",
        objection=objection,
        situation=situation,
        long_term=long_term,
        message_text=message,
    )
    result: RetrievalResult = retrieve_for_turn(ctx)

    do_not = [
        "не спрашивать уже известные поля повторно",
        "не обещать неподтверждённую availability/цену владельца",
        "не имитировать ответ владельца",
        "не писать при HUMAN_HANDOFF",
    ]
    goal = "продолжить квалификацию естественно"
    action = "qualify"
    if session.awaiting_owner:
        action = "wait_owner"
        goal = "держать клиента в курсе, не дублировать owner request"
    elif session.chosen and "check_in" not in known:
        action = "ask_check_in"
        goal = "получить дату заезда для цены и owner check"
    elif objection:
        action = "handle_objection"
        goal = "снять возражение без спора и сохранить диалог"

    return ConversationStrategy(
        current_stage=dialog_stage(session),
        known_fields=known,
        missing_fields=missing,
        client_intent=intent_u,
        object_context=lead.preferred_object_id or "",
        rental_policy=policy or "",
        recommended_action=action,
        do_not_do=do_not,
        knowledge_refs=result.selected_ids,
        response_goal=goal,
        knowledge_compact=result.compact_prompt(),
    )


def build_owner_strategy(
    session: Session,
    *,
    object_source: str = "",
    message: str = "",
) -> OwnerStrategy:
    from agent6_qualifier.rental_policy import evaluate_rental_policy, listing_source_kind

    lead = session.lead
    listing = session.chosen
    src = object_source or listing_source_kind(listing)
    pol = evaluate_rental_policy(listing, lead)
    dates = ""
    if lead.check_in:
        dates = lead.check_in.isoformat()
        if lead.check_out:
            dates += f" — {lead.check_out.isoformat()}"
    duration = f"{lead.stay_months} months" if lead.stay_months is not None else (
        "year_contract" if lead.check_in and not lead.check_out else "unknown"
    )
    verify = ["availability", "current_terms"]
    if src == "facebook":
        verify += ["actual_monthly_price", "requested_duration"]
    if src == "airbnb" and (lead.stay_months or 0) >= 6:
        verify += ["long_term_possible", "monthly_or_long_term_price"]

    ctx = RetrievalContext(
        agent="AGENT7",
        stage="owner_outreach",
        intent="owner_verification",
        object_source=src,
        rental_policy=pol.policy,
        current_object=lead.preferred_object_id or "",
        long_term=(lead.stay_months is not None and float(lead.stay_months) >= 6),
        message_text=message,
        situation="owner_check",
    )
    result = retrieve_for_turn(ctx)
    return OwnerStrategy(
        object_id=lead.preferred_object_id or (listing.object_id if listing else ""),
        source=src,
        rental_policy=pol.policy,
        requested_dates=dates,
        duration=duration,
        verification_needed=verify,
        questions_for_owner=verify,
        knowledge_refs=result.selected_ids,
        do_not_do=[
            "не передавать клиентский budget владельцу",
            "не угадывать при AMBIGUOUS/NOT_FOUND correlation",
            "не писать на client phone как owner",
            "не повторно обрабатывать duplicate owner reply",
        ],
        knowledge_compact=result.compact_prompt(),
    )
