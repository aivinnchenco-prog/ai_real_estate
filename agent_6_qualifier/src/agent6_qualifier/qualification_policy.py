"""Wave 3 turn pre-processing: confidence, corrections, conflicts, reactions, repair.

``Qualifier.handle_message`` calls :func:`preprocess_turn` once per inbound
message. Everything here is deterministic — the LLM extract is an input, never
an authority. The Wave 1/2 decision tree that follows is left intact; this
layer only enriches session state and can offer a reply for the turns it owns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import constraints as constraints_mod
from . import contradictions as contradictions_mod
from . import lead_temperature as temperature_mod
from . import reactions as reactions_mod
from . import repair as repair_mod
from .corrections import (
    CorrectionResult,
    apply_correction,
    detect_correction,
    filter_update_to_correction,
    is_explicit_restatement,
)
from .qualification_meta import TRACKED_SLOTS, SlotSource, sync_from_update

if TYPE_CHECKING:
    from .qualifier import Session

# Slots whose correction means "the search changed", not "start over".
_CRITERIA_SLOTS = frozenset({
    "budget", "districts", "bedrooms", "guests",
    "check_in", "check_out", "stay_months",
})

# Client explicitly takes the dialogue back from the manager.
_RESUME_SELF_SERVICE = re.compile(
    r"продолж\w*\s+сам|готов\w*\s+продолжить\s+сам|дальше\s+сам|"
    r"без\s+менеджера|не\s+нужен\s+менеджер|менеджер\s+не\s+нужен",
    re.IGNORECASE,
)


@dataclass
class TurnPolicy:
    """Deterministic decisions taken before the Wave 1/2 flow runs."""
    events: list[str] = field(default_factory=list)
    correction: CorrectionResult = field(default_factory=CorrectionResult)
    corrected_slots: list[str] = field(default_factory=list)
    contradictions: list = field(default_factory=list)
    clarification: str = ""
    clarification_slot: str = ""
    reaction: reactions_mod.DetectedReaction | None = None
    reaction_object_id: str = ""
    repair: repair_mod.RepairAssessment = field(
        default_factory=repair_mod.RepairAssessment
    )
    forced_intent: str | None = None
    resumed_from_human: bool = False
    effective_update: dict = field(default_factory=dict)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.clarification)


def _explicit_slots(message: str, update: dict, corrected: list[str]) -> set[str]:
    """Slots we can prove came from an explicit client statement."""
    explicit = set(corrected)
    if is_explicit_restatement(message):
        explicit.update(k for k in (update or {}) if k in TRACKED_SLOTS)
    return explicit


def _touched_slots(update: dict, corrected: list[str]) -> list[str]:
    touched = [k for k in (update or {}) if k in TRACKED_SLOTS]
    for slot in corrected:
        if slot not in touched:
            touched.append(slot)
    return touched


def client_resumes_self_service(message: str) -> bool:
    return bool(_RESUME_SELF_SERVICE.search(message or ""))


def _handle_reaction(session: Session, message: str, policy: TurnPolicy) -> None:
    reaction = reactions_mod.detect_reaction(message)
    if reaction is None:
        return
    object_id = reactions_mod.reaction_target_object(session)
    reactions_mod.record_reaction(session, object_id, reaction)
    policy.reaction = reaction
    policy.reaction_object_id = object_id

    # «этот район нравится, но дом не подходит» — keep the area as a soft plus.
    if reaction.keeps_district:
        district = ""
        if session.chosen is not None:
            district = session.chosen.district
        elif session.lead.districts:
            district = session.lead.districts[0]
        if district:
            reactions_mod.apply_preference_signals(
                session,
                [reactions_mod.PreferenceSignal("district", district, "positive", 1.5)],
            )

    label = reaction.reaction_type.value
    policy.events.append(
        f"Реакция клиента: {label}"
        + (f" по объекту {object_id}" if object_id else "")
    )


def _handle_clarification_answer(
    session: Session, message: str, policy: TurnPolicy
) -> None:
    pending = getattr(session, "pending_clarification_slot", "") or ""
    if not pending:
        return
    verdict = contradictions_mod.answers_clarification(message)
    has_new_value = any(
        k in (policy.effective_update or {}) for k in (pending, "check_out", "stay_months")
    )
    if verdict is None and not has_new_value and not policy.corrected_slots:
        return
    contradictions_mod.resolve_contradiction(session, pending)
    session.pending_clarification_slot = ""
    policy.events.append(f"Противоречие снято: {pending}")
    # The client just told us which value is real — that is a confirmation.
    from .qualification_meta import record_slot

    value = getattr(session.lead, pending, None)
    if value is not None:
        record_slot(
            session, pending, value, SlotSource.EXPLICIT_CLIENT,
            is_correction=True, message_snippet=message,
        )


def preprocess_turn(session: Session, message: str, update: dict) -> TurnPolicy:
    """Apply the Wave 3 policy layer to one inbound message."""
    from .brain import apply_update

    policy = TurnPolicy()
    raw_update = dict(update or {})

    # 1. Corrections are detected before anything is written, so an LLM extract
    #    can never widen «не 150, а 200» into a full criteria rewrite.
    policy.correction = detect_correction(message, session.lead)
    policy.effective_update = filter_update_to_correction(raw_update, policy.correction)

    # 2. Conflicts are computed against the *previous* values.
    detected = contradictions_mod.detect_contradictions(
        session,
        policy.effective_update,
        message,
        corrected_slots=policy.correction.corrected_slots,
    )

    # 3. Write values: extract first, deterministic correction on top.
    apply_update(session.lead, policy.effective_update, message=message)
    policy.corrected_slots = apply_correction(session.lead, policy.correction)
    if policy.corrected_slots:
        policy.events.append(
            "Исправление клиента: " + ", ".join(policy.corrected_slots)
        )

    # 4. Persist conflict state and let self-resolving ones take effect.
    contradictions_mod.apply_self_resolving(session, detected)
    contradictions_mod.record_contradictions(session, detected)
    policy.contradictions = detected

    # 5. Confidence + hard/soft classification for everything touched this turn.
    sync_from_update(
        session,
        policy.effective_update,
        message=message,
        corrected_slots=policy.corrected_slots,
        explicit_slots=_explicit_slots(
            message, policy.effective_update, policy.corrected_slots
        ),
    )
    constraints_mod.classify_and_record(
        session,
        _touched_slots(policy.effective_update, policy.corrected_slots),
        message,
    )

    # 6. Object reactions and preference signals.
    _handle_reaction(session, message, policy)

    # 7. Did this message answer an open clarification?
    _handle_clarification_answer(session, message, policy)

    # 8. A criteria correction is a criteria change (policy §G): the active
    #    owner request must be archived instead of blocking the new numbers.
    if any(slot in _CRITERIA_SLOTS for slot in policy.corrected_slots):
        policy.forced_intent = "CHANGE_CRITERIA"

    # 9. Client explicitly takes over from the manager.
    if session.human_handoff_active and client_resumes_self_service(message):
        session.human_handoff_active = False
        session.handoff_to_human = False
        policy.resumed_from_human = True
        policy.events.append("Клиент продолжает диалог с ботом")

    # 10. Repair assessment (never handoff on the first misunderstanding).
    policy.repair = repair_mod.assess(
        session,
        message,
        raw_update,
        has_unresolved_contradiction=contradictions_mod.has_unresolved(session),
    )
    if policy.repair.triggered:
        policy.events.append(
            f"Repair: {policy.repair.reason.value} "
            f"({policy.repair.misunderstanding_count}/"
            f"{repair_mod.max_repair_attempts()})"
        )

    # 11. Open clarification to ask this turn, if any.
    pending = contradictions_mod.first_unresolved(session)
    if pending and pending.get("question"):
        policy.clarification = pending["question"]
        policy.clarification_slot = pending.get("slot", "")

    temperature_mod.refresh(session)
    return policy


# ---------- reply builders owned by the policy layer ----------

def build_repair_understanding(session: Session) -> str:
    """One short line describing what the agent now believes (policy §13)."""
    lead = session.lead
    bits: list[str] = []
    if session.chosen is not None:
        bits.append(f"объект {session.chosen.object_id}")
    elif lead.preferred_object_id:
        bits.append(f"объект {lead.preferred_object_id}")
    if lead.districts:
        bits.append(", ".join(lead.districts))
    if lead.budget is not None:
        bits.append(f"до {lead.budget:,.0f} ฿".replace(",", " "))
    if lead.check_in is not None:
        bits.append(f"заезд {lead.check_in.strftime('%d.%m.%Y')}")
    if not bits:
        return "Понял, давайте зафиксируем запрос заново."
    return "Понял. Сейчас ищем: " + ", ".join(bits) + "."


def build_reaction_reply(session: Session, policy: TurnPolicy) -> tuple[str, list[str]]:
    """Acknowledge a reaction and move to the next useful action."""
    reaction = policy.reaction
    assert reaction is not None
    lines = [reactions_mod.acknowledge_reaction(reaction)]
    events: list[str] = []

    district = ""
    if reaction.keeps_district:
        if session.chosen is not None and session.chosen.district:
            district = session.chosen.district
        elif session.lead.districts:
            district = session.lead.districts[0]
    if district:
        lines.append(
            f"Район {district} оставляем — подберу другие варианты там."
        )

    if (
        reaction.reaction_type in reactions_mod.REJECTING_REACTIONS
        and policy.reaction_object_id
    ):
        rejected_chosen = (
            session.chosen is not None
            and session.chosen.object_id == policy.reaction_object_id
        )
        if rejected_chosen:
            session.chosen = None
            session.lead.preferred_object_id = ""
            session.only_chosen = False
            session.price_quoted = False
            session.links_sent = False
            session.wants_selection = True
            events.append(f"Объект {policy.reaction_object_id} отклонён клиентом")

    return "\n\n".join(lines), events


def reaction_refine_question(session: Session, policy: TurnPolicy) -> str:
    """The single narrowing question a reaction implies, if any."""
    reaction = policy.reaction
    if reaction is None:
        return ""
    if (
        reaction.reaction_type == reactions_mod.ReactionType.BAD_LOCATION
        and not session.lead.districts
    ):
        return "Какой район вам подходит?"
    if (
        reaction.reaction_type == reactions_mod.ReactionType.TOO_EXPENSIVE
        and session.lead.budget is None
    ):
        return "Какой бюджет в месяц вам комфортен?"
    return ""


def reaction_owns_turn(policy: TurnPolicy) -> bool:
    """LIKE is remembered silently; rejections and style hints drive the reply."""
    reaction = policy.reaction
    if reaction is None:
        return False
    if reaction.reaction_type in reactions_mod.REJECTING_REACTIONS:
        return True
    return reaction.reaction_type in (
        reactions_mod.ReactionType.STYLE_LIKE,
        reactions_mod.ReactionType.TOO_CHEAP,
        reactions_mod.ReactionType.OTHER,
    )
