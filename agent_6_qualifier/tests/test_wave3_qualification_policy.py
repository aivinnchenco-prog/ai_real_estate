"""Unit tests for the Wave 3 qualification policy modules.

Covers each module in isolation (confidence, corrections, contradictions,
reactions, constraints, repair, temperature, handoff note, morphology) plus
session backward compatibility for sessions written before Wave 3.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from agent6_qualifier.active_request import (
    NextBestAction,
    build_active_request,
    compute_next_best_action,
)
from agent6_qualifier.constraints import (
    ConstraintDecision,
    Strength,
    classify_and_record,
    classify_constraint,
    hard_constraints,
    record_constraint,
    soft_preferences,
)
from agent6_qualifier.contradictions import (
    ContradictionType,
    build_clarification,
    detect_contradictions,
    first_unresolved,
    has_unresolved,
    record_contradictions,
    resolve_contradiction,
)
from agent6_qualifier.corrections import (
    apply_correction,
    detect_correction,
    is_explicit_restatement,
)
from agent6_qualifier.handoff_note import HANDOFF_MARKER, build_handoff_note
from agent6_qualifier.lead_temperature import LeadTemperature, compute_temperature
from agent6_qualifier.matching import (
    find_alternatives,
    preference_score,
    rejected_trait_penalty,
)
from agent6_qualifier.models import Availability, LeadProfile, Listing
from agent6_qualifier.morphology import (
    bedrooms_phrase,
    guests_phrase,
    months_phrase,
    nights_word,
    plural_form,
)
from agent6_qualifier.qualification_meta import (
    Confidence,
    SlotSource,
    backfill_from_lead,
    clear_search_slots,
    confidence_snapshot,
    may_overwrite,
    record_slot,
    slot_confidence,
    sync_from_update,
)
from agent6_qualifier.reactions import (
    PreferenceSignal,
    ReactionType,
    acknowledge_reaction,
    detect_reaction,
    mark_shown,
    record_reaction,
    reset_reaction_memory,
)
from agent6_qualifier.repair import (
    RepairReason,
    detect_repair_signal,
    max_repair_attempts,
    register_repair,
)
from agent6_qualifier.qualifier import Qualifier, Session


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _listing(oid: str, **kw) -> Listing:
    base = dict(
        object_id=oid,
        title="Вилла",
        district="Раваи",
        housing_type="вилла",
        rooms=2,
        price_month=50000,
    )
    base.update(kw)
    return Listing(**base)


A = _listing("F_1")
B = _listing("F_2", price_month=52000)
C = _listing("F_3", district="Банг Тао", price_month=90000)


def make_qualifier(listings=None) -> Qualifier:
    items = listings if listings is not None else [A, B, C]
    by_id = {l.object_id: l for l in items}
    return Qualifier(find_by_id=by_id.get, fetch_all=lambda: items)


@pytest.fixture
def session() -> Session:
    return Session(chat_id="w3")


# --------------------------------------------------------------------------
# 1-2. slot confidence
# --------------------------------------------------------------------------

def test_explicit_client_is_confirmed(session):
    session.lead.budget = 200000
    record_slot(session, "budget", 200000, SlotSource.EXPLICIT_CLIENT)
    assert slot_confidence(session, "budget") is Confidence.CONFIRMED


@pytest.mark.parametrize(
    "source",
    [SlotSource.INFERRED, SlotSource.PUBLICATION, SlotSource.CRM,
     SlotSource.PREVIOUS_CONTEXT],
)
def test_non_explicit_sources_are_inferred(session, source):
    session.lead.budget = 100000
    record_slot(session, "budget", 100000, source)
    assert slot_confidence(session, "budget") is Confidence.INFERRED


def test_missing_slot_is_unknown(session):
    assert slot_confidence(session, "budget") is Confidence.UNKNOWN
    assert slot_confidence(session, "districts") is Confidence.UNKNOWN


def test_confirmed_not_silently_replaced_by_inferred(session):
    session.lead.budget = 200000
    record_slot(session, "budget", 200000, SlotSource.EXPLICIT_CLIENT)
    assert may_overwrite(session, "budget", SlotSource.INFERRED) is False
    assert may_overwrite(session, "budget", SlotSource.EXPLICIT_CLIENT) is True


def test_explicit_correction_overrides_confirmed(session):
    session.lead.budget = 200000
    record_slot(session, "budget", 200000, SlotSource.EXPLICIT_CLIENT)
    assert may_overwrite(
        session, "budget", SlotSource.INFERRED, is_correction=True
    ) is True


def test_slot_meta_records_value_source_and_timestamp(session):
    entry = record_slot(session, "guests", 4, SlotSource.EXPLICIT_CLIENT)
    assert entry["value"] == 4
    assert entry["source"] == "explicit_client"
    assert entry["confidence"] == "CONFIRMED"
    assert entry["updated_at"]


def test_dates_serialise_as_iso(session):
    entry = record_slot(session, "check_in", date(2026, 9, 1), SlotSource.EXPLICIT_CLIENT)
    assert entry["value"] == "2026-09-01"
    json.dumps(entry)  # must stay session-serialisable


def test_correction_history_tracked(session):
    record_slot(session, "budget", 150000, SlotSource.EXPLICIT_CLIENT)
    record_slot(session, "budget", 200000, SlotSource.EXPLICIT_CLIENT, is_correction=True,
                message_snippet="не 150, а 200")
    history = session.slot_meta["budget"]["correction_history"]
    assert history[-1]["old_value"] == 150000
    assert history[-1]["new_value"] == 200000


def test_sync_from_update_marks_llm_extract_inferred(session):
    session.lead.budget = 90000
    sync_from_update(session, {"budget": 90000}, message="думаю тысяч 90")
    assert slot_confidence(session, "budget") is Confidence.INFERRED


def test_sync_from_update_marks_explicit_slots_confirmed(session):
    session.lead.guests = 4
    sync_from_update(session, {"guests": 4}, message="я уже говорил, нас 4",
                     explicit_slots=["guests"])
    assert slot_confidence(session, "guests") is Confidence.CONFIRMED


def test_confidence_snapshot_covers_tracked_slots(session):
    snap = confidence_snapshot(session)
    for slot in ("check_in", "check_out", "stay_months", "budget", "districts",
                 "bedrooms", "guests", "pets", "preferred_object_id"):
        assert snap[slot] == "UNKNOWN"


def test_clear_search_slots_drops_criteria(session):
    record_slot(session, "budget", 150000, SlotSource.EXPLICIT_CLIENT)
    clear_search_slots(session)
    assert slot_confidence(session, "budget") is Confidence.UNKNOWN


# --------------------------------------------------------------------------
# 3. corrections
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message,slot,value",
    [
        ("не 150, а 200 тысяч", "budget", 200000),
        ("не Банг Тао, теперь Раваи", "districts", ["Раваи"]),
        ("нас будет не 2, а 4", "guests", 4),
    ],
)
def test_detect_correction_targets_single_slot(message, slot, value):
    result = detect_correction(message)
    assert result.corrected_slots == [slot]
    assert result.target_values[slot] == value


def test_correction_of_check_in_day():
    result = detect_correction("заезжаем не 10-го, а 15-го",
                               LeadProfile(check_in=date(2026, 9, 10)),
                               today=date(2026, 9, 1))
    assert result.corrected_slots == ["check_in"]
    assert result.target_values["check_in"].day == 15


def test_apply_correction_touches_only_target_slot():
    lead = LeadProfile(budget=150000, districts=["Раваи"], guests=2,
                       check_in=date(2026, 9, 1))
    applied = apply_correction(lead, detect_correction("не 150, а 200 тысяч"))
    assert applied == ["budget"]
    assert lead.budget == 200000
    assert lead.districts == ["Раваи"]
    assert lead.guests == 2
    assert lead.check_in == date(2026, 9, 1)


def test_plain_statement_is_not_a_correction():
    assert detect_correction("бюджет 150 тысяч").corrected_slots == []
    assert detect_correction("хочу виллу в Раваи").corrected_slots == []


def test_explicit_restatement_detected():
    assert is_explicit_restatement("я уже говорил, бюджет 200") is True
    assert is_explicit_restatement("бюджет 200") is False


# --------------------------------------------------------------------------
# 4-5. contradictions
# --------------------------------------------------------------------------

def test_ambiguous_stay_conflict_asks_one_question(session):
    session.lead.check_in = date(2026, 9, 1)
    session.lead.stay_months = 12
    found = detect_contradictions(
        session, {"check_out": date(2026, 12, 1)}, "до декабря"
    )
    assert found
    item = found[0]
    assert item.type is ContradictionType.STAY_CONFLICT
    assert item.needs_clarification is True
    question = build_clarification(item)
    assert question.count("?") == 1


def test_explicit_correction_is_not_a_contradiction(session):
    session.lead.stay_months = 12
    found = detect_contradictions(
        session, {"stay_months": 3}, "не на год, а на 3 месяца",
        corrected_slots=["stay_months"],
    )
    assert not [c for c in found if c.needs_clarification]


def test_replacement_marker_suppresses_clarification(session):
    session.lead.budget = 150000
    found = detect_contradictions(session, {"budget": 200000}, "теперь бюджет 200")
    assert not [c for c in found if c.needs_clarification]


def test_resolve_contradiction_marks_resolved(session):
    session.lead.check_in = date(2026, 9, 1)
    session.lead.stay_months = 12
    found = detect_contradictions(session, {"check_out": date(2026, 12, 1)}, "до декабря")
    record_contradictions(session, found)
    assert has_unresolved(session) is True
    resolve_contradiction(session, first_unresolved(session)["slot"])
    assert has_unresolved(session) is False


def test_clarification_never_reopens_whole_questionnaire(session):
    session.lead.check_in = date(2026, 9, 1)
    session.lead.stay_months = 12
    found = detect_contradictions(session, {"check_out": date(2026, 12, 1)}, "до декабря")
    text = build_clarification(found[0])
    for forbidden in ("Бюджет в месяц", "Сколько спален", "Сколько гостей"):
        assert forbidden not in text


# --------------------------------------------------------------------------
# 6-7. reactions and preference signals
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message,expected",
    [
        ("этот нравится", ReactionType.LIKE),
        ("этот вариант нормальный", ReactionType.LIKE),
        ("слишком дорого", ReactionType.TOO_EXPENSIVE),
        ("почему так дешево", ReactionType.TOO_CHEAP),
        ("слишком дёшево", ReactionType.TOO_CHEAP),
        ("не нравится интерьер", ReactionType.STYLE_DISLIKE),
        ("хочу современнее", ReactionType.STYLE_LIKE),
        ("далеко", ReactionType.BAD_LOCATION),
        ("этот не подходит", ReactionType.DISLIKE),
        ("покажи другой", ReactionType.DISLIKE),
        ("нужен больше бассейн", ReactionType.NO_POOL),
    ],
)
def test_detect_reaction_examples(message, expected):
    detected = detect_reaction(message)
    assert detected is not None, message
    assert detected.reaction_type is expected


def test_positive_district_reaction_keeps_district(session):
    detected = detect_reaction("район хороший")
    assert detected is not None
    assert detected.keeps_district is True
    session.lead.districts = ["Раваи"]
    record_reaction(session, "F_1", detected)
    assert any(p["kind"] == "district" and p["value"] == "Раваи"
               for p in session.positive_preferences)


def test_similar_to_this_is_a_like():
    detected = detect_reaction("хочу что-то похожее на этот")
    assert detected is not None
    assert detected.reaction_type in {ReactionType.LIKE, ReactionType.STYLE_LIKE}


def test_positive_phrase_not_misread_as_dislike():
    detected = detect_reaction("мне нравится этот вариант")
    assert detected is not None
    assert detected.reaction_type is ReactionType.LIKE


def test_record_reaction_updates_memory(session):
    record_reaction(session, "F_1", detect_reaction("слишком дорого"))
    assert "F_1" in session.rejected_object_ids
    assert session.object_reactions[-1]["object_id"] == "F_1"
    assert session.object_reactions[-1]["timestamp"]
    kinds = {p["kind"] for p in session.negative_preferences}
    assert "price" in kinds


def test_like_records_liked_object(session):
    record_reaction(session, "F_1", detect_reaction("этот нравится"))
    assert session.liked_object_ids == ["F_1"]
    assert "F_1" not in session.rejected_object_ids


def test_reaction_is_not_promoted_to_hard_constraint(session):
    record_reaction(session, "F_1", detect_reaction("слишком дорого"))
    assert not hard_constraints(session)


def test_acknowledge_reaction_is_short_and_not_defensive(session):
    text = acknowledge_reaction(detect_reaction("слишком дорого"))
    assert text
    assert len(text) < 160
    for forbidden in ("извин", "я уже спрашивал", "но ведь"):
        assert forbidden not in text.lower()


def test_mark_shown_tracks_objects(session):
    mark_shown(session, ["F_1", "F_2"])
    mark_shown(session, ["F_2", "F_3"])
    assert session.shown_object_ids == ["F_1", "F_2", "F_3"]


def test_reset_reaction_memory_is_search_scoped(session):
    record_reaction(session, "F_1", detect_reaction("этот не подходит"))
    session.lead.full_name = "Иван"
    reset_reaction_memory(session)
    assert session.rejected_object_ids == []
    assert session.liked_object_ids == []
    assert session.lead.full_name == "Иван"  # identity survives


# --------------------------------------------------------------------------
# 8. hard vs soft
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "slot,message,expected",
    [
        ("districts", "только Банг Тао", Strength.HARD),
        ("districts", "лучше Банг Тао", Strength.SOFT),
        ("budget", "не выше 200к", Strength.HARD),
        ("budget", "желательно до 200к", Strength.SOFT),
        ("bedrooms", "обязательно 4 спальни", Strength.HARD),
        ("bedrooms", "лучше 4 спальни", Strength.SOFT),
    ],
)
def test_constraint_strength(slot, message, expected):
    assert classify_constraint(slot, message) is expected


def test_record_constraint_splits_hard_and_soft(session):
    record_constraint(session, ConstraintDecision(
        slot="districts", strength=Strength.HARD, value=["Банг Тао"]))
    record_constraint(session, ConstraintDecision(
        slot="budget", strength=Strength.SOFT, value=200000))
    assert {i["slot"] for i in hard_constraints(session)} == {"districts"}
    assert {i["slot"] for i in soft_preferences(session)} == {"budget"}


def test_constraint_upgrade_from_soft_to_hard(session):
    record_constraint(session, ConstraintDecision(
        slot="districts", strength=Strength.SOFT, value=["Раваи"]))
    record_constraint(session, ConstraintDecision(
        slot="districts", strength=Strength.HARD, value=["Раваи"]))
    assert {i["slot"] for i in hard_constraints(session)} == {"districts"}
    assert not soft_preferences(session)


def test_classify_and_record_from_live_message(session):
    session.lead.districts = ["Банг Тао"]
    classify_and_record(session, ["districts"], "только Банг Тао")
    assert {i["slot"] for i in hard_constraints(session)} == {"districts"}


# --------------------------------------------------------------------------
# 9-10. matching integration and rejected memory
# --------------------------------------------------------------------------

def test_preference_score_rewards_positive_signal():
    lead = LeadProfile(guests=2)
    positive = [{"kind": "district", "value": "Раваи"}]
    assert preference_score(A, lead, positive=positive) > preference_score(C, lead, positive=positive)


def test_rejected_trait_penalty_lowers_similar_objects():
    assert rejected_trait_penalty(B, [A]) > rejected_trait_penalty(C, [A])


def test_rejected_object_excluded_from_next_shortlist():
    lead = LeadProfile(guests=2, districts=["Раваи"])
    picks = find_alternatives([A, B, C], lead, chosen=None, limit=3, exclude_ids=["F_1"])
    assert "F_1" not in [p.object_id for p in picks]


def test_hard_filters_survive_preference_ranking():
    lead = LeadProfile(guests=2, budget=60000)
    negative = [{"kind": "price", "value": ""}]
    picks = find_alternatives([A, B, C], lead, negative_preferences=negative)
    assert "F_3" not in [p.object_id for p in picks]  # 90k over the 60k budget


def test_soft_negative_does_not_hide_a_good_object():
    lead = LeadProfile(guests=2)
    negative = [{"kind": "style", "value": "modern"}]
    picks = find_alternatives([A, B, C], lead, negative_preferences=negative)
    assert picks, "soft negative must rank down, not filter out"


def test_rejected_returned_only_via_explicit_fallback():
    lead = LeadProfile(guests=2, districts=["Раваи"])
    none_left = find_alternatives([A], lead, exclude_ids=["F_1"])
    assert none_left == []
    fallback = find_alternatives([A], lead, exclude_ids=["F_1"],
                                 allow_rejected_fallback=True)
    assert [p.object_id for p in fallback] == ["F_1"]


# --------------------------------------------------------------------------
# 12-14. repair
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message",
    ["ты понимаешь?", "я уже сказал", "я не это имею в виду", "нет, не так",
     "ты не понял", "читайте внимательнее"],
)
def test_explicit_misunderstanding_signals(session, message):
    assert detect_repair_signal(session, message, {}) is RepairReason.EXPLICIT_MISUNDERSTANDING


def test_repeated_client_request_triggers_repair(session):
    session.history = [{"role": "user", "text": "хочу виллу в Раваи на год"}]
    assert detect_repair_signal(session, "хочу виллу в Раваи на год", {}) is (
        RepairReason.REPEATED_CLIENT_REQUEST
    )


def test_repeated_bot_reply_triggers_repair(session):
    reply = "Подскажите, пожалуйста, дату заезда."
    session.history = [
        {"role": "assistant", "text": reply},
        {"role": "assistant", "text": reply},
    ]
    assert detect_repair_signal(session, "хм", {}) is RepairReason.REPEATED_BOT_REPLY


def test_neutral_message_is_not_repair(session):
    assert detect_repair_signal(session, "бюджет 150 тысяч", {"budget": 150000}) is None


def test_first_misunderstanding_never_hands_off(session):
    assessment = register_repair(session, RepairReason.EXPLICIT_MISUNDERSTANDING)
    assert assessment.should_handoff is False
    assert session.misunderstanding_count == 1
    assert session.repair_mode is True


def test_handoff_only_past_threshold(session, monkeypatch):
    monkeypatch.setenv("AGENT6_MAX_REPAIR_ATTEMPTS", "2")
    assert max_repair_attempts() == 2
    assert register_repair(session, RepairReason.EXPLICIT_MISUNDERSTANDING).should_handoff is False
    assert register_repair(session, RepairReason.EXPLICIT_MISUNDERSTANDING).should_handoff is False
    assert register_repair(session, RepairReason.EXPLICIT_MISUNDERSTANDING).should_handoff is True


def test_repair_reply_is_short_with_one_question():
    q = make_qualifier()
    s = Session(chat_id="rp", awaiting_owner=True, chosen=A)
    s.lead.preferred_object_id = "F_1"
    turn = q.handle_message(s, "ты понимаешь? я хочу новый дом", {})
    assert turn.reply_draft.count("?") <= 1
    assert "жду ответ" not in turn.reply_draft.lower()
    assert s.repair_mode is True
    assert turn.handoff_to_human is False


# --------------------------------------------------------------------------
# 15. structured handoff note
# --------------------------------------------------------------------------

def test_handoff_note_has_all_sections(session):
    session.lead = LeadProfile(check_in=date(2026, 9, 1), guests=2, budget=90000,
                               districts=["Раваи"], preferred_object_id="F_1")
    session.chosen = A
    mark_shown(session, ["F_2"])
    record_reaction(session, "F_2", detect_reaction("слишком дорого"))
    record_reaction(session, "F_1", detect_reaction("этот нравится"))
    note = build_handoff_note(session, reason="MANAGER_REQUESTED",
                              last_client_message="позовите менеджера")
    assert note.startswith(HANDOFF_MARKER)
    for section in ("Intent:", "Active request:", "Hard constraints:",
                    "Soft preferences:", "Shown objects:", "Liked:", "Rejected:",
                    "Pending owner checks:", "Lead temperature:",
                    "Reason for handoff:", "Next best action:",
                    "Last client message:"):
        assert section in note, f"missing section {section}"
    assert "F_2" in note and "F_1" in note
    assert "MANAGER_REQUESTED" in note


def test_handoff_note_is_plain_text(session):
    note = build_handoff_note(session)
    assert "\n" in note
    assert note.strip() == note


# --------------------------------------------------------------------------
# 16. lead temperature
# --------------------------------------------------------------------------

def test_cold_lead(session):
    assert compute_temperature(session) is LeadTemperature.COLD


def test_warm_lead_with_criteria_only(session):
    session.lead = LeadProfile(check_in=date(2026, 9, 1), guests=2, budget=90000,
                               districts=["Раваи"])
    assert compute_temperature(session) is LeadTemperature.WARM


def test_hot_lead_with_object_and_owner_check(session):
    session.lead = LeadProfile(check_in=date(2026, 9, 1), guests=2, budget=90000,
                               preferred_object_id="F_1")
    session.chosen = A
    session.awaiting_owner = True
    assert compute_temperature(session) is LeadTemperature.HOT


def test_booking_intent_is_hot(session):
    session.booking_intent = True
    session.lead = LeadProfile(check_in=date(2026, 9, 1), guests=2)
    assert compute_temperature(session) is LeadTemperature.HOT


def test_stalled_lead(session, monkeypatch):
    monkeypatch.setenv("AGENT6_LEAD_STALLED_HOURS", "48")
    session.lead = LeadProfile(check_in=date(2026, 9, 1), guests=2, budget=90000)
    session.last_client_message_at = (
        datetime.now(timezone.utc) - timedelta(hours=72)
    ).isoformat()
    assert compute_temperature(session) is LeadTemperature.STALLED


def test_temperature_never_reaches_the_client():
    q = make_qualifier()
    s = Session(chat_id="lt")
    turn = q.handle_message(s, "здравствуйте", {})
    for label in ("COLD", "WARM", "HOT", "STALLED", "temperature"):
        assert label not in (turn.reply_draft or "")


# --------------------------------------------------------------------------
# 17-18. next best action and active request snapshot
# --------------------------------------------------------------------------

def test_next_best_action_enum_has_wave3_members():
    for name in ("ASK_CRITICAL_SLOT", "SHOW_MATCHES", "REFINE_MATCHES", "CHECK_OWNER",
                 "WAIT_OWNER", "OFFER_ALTERNATIVES", "START_BOOKING",
                 "REPAIR_DIALOGUE", "HANDOFF_HUMAN", "FOLLOW_UP", "NONE"):
        assert hasattr(NextBestAction, name), name


def test_repair_mode_drives_next_action(session):
    session.repair_mode = True
    assert compute_next_best_action(session) is NextBestAction.REPAIR_DIALOGUE


def test_handoff_drives_next_action(session):
    session.handoff_to_human = True
    assert compute_next_best_action(session) is NextBestAction.HANDOFF_HUMAN


def test_unresolved_contradiction_asks_before_showing(session):
    session.lead = LeadProfile(check_in=date(2026, 9, 1), stay_months=12, guests=2,
                               budget=90000, districts=["Раваи"])
    found = detect_contradictions(session, {"check_out": date(2026, 12, 1)}, "до декабря")
    record_contradictions(session, found)
    assert compute_next_best_action(session) is NextBestAction.ASK_CRITICAL_SLOT


def test_active_request_snapshot_contains_wave3_fields(session):
    snapshot = build_active_request(session)
    for key in ("slot_confidence", "hard_constraints", "soft_preferences",
                "shown_object_ids", "liked_object_ids", "rejected_object_ids",
                "contradictions", "repair_mode", "lead_temperature",
                "next_best_action"):
        assert key in snapshot, f"missing {key}"


def test_active_request_snapshot_is_json_serialisable(session):
    session.lead = LeadProfile(check_in=date(2026, 9, 1), guests=2)
    record_reaction(session, "F_1", detect_reaction("слишком дорого"))
    json.dumps(build_active_request(session), ensure_ascii=False, default=str)


# --------------------------------------------------------------------------
# 19. search context isolation
# --------------------------------------------------------------------------

def test_new_search_clears_reactions_but_keeps_identity():
    q = make_qualifier()
    s = Session(chat_id="ns", asked_core=True)
    s.lead = LeadProfile(full_name="Иван", whatsapp="+79990000000",
                         check_in=date(2026, 9, 1), guests=2, districts=["Раваи"])
    record_reaction(s, "F_1", detect_reaction("этот не подходит"))
    q.handle_message(s, "давай искать новый дом, забудьте про старый запрос", {})
    assert s.rejected_object_ids == []
    assert s.lead.full_name == "Иван"
    assert s.lead.whatsapp == "+79990000000"


# --------------------------------------------------------------------------
# 21. russian morphology
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "n,expected",
    [(1, "1 спальня"), (2, "2 спальни"), (3, "3 спальни"), (4, "4 спальни"),
     (5, "5 спален"), (11, "11 спален"), (21, "21 спальня"), (22, "22 спальни")],
)
def test_bedrooms_morphology(n, expected):
    assert bedrooms_phrase(n) == expected


@pytest.mark.parametrize(
    "n,expected",
    [(1, "1 гость"), (2, "2 гостя"), (4, "4 гостя"), (5, "5 гостей"),
     (11, "11 гостей"), (21, "21 гость")],
)
def test_guests_morphology(n, expected):
    assert guests_phrase(n) == expected


@pytest.mark.parametrize(
    "n,expected",
    [(1, "1 месяц"), (2, "2 месяца"), (5, "5 месяцев"), (11, "11 месяцев")],
)
def test_months_morphology(n, expected):
    assert months_phrase(n) == expected


def test_half_month_is_not_mangled():
    assert months_phrase(1.5) == "1.5 месяца"


@pytest.mark.parametrize("n,expected", [(1, "ночь"), (2, "ночи"), (5, "ночей")])
def test_nights_morphology(n, expected):
    assert nights_word(n) == expected


def test_plural_form_generic():
    assert plural_form(1, "день", "дня", "дней") == "день"
    assert plural_form(3, "день", "дня", "дней") == "дня"
    assert plural_form(15, "день", "дня", "дней") == "дней"


# --------------------------------------------------------------------------
# 25. session backward compatibility
# --------------------------------------------------------------------------

def test_pre_wave3_session_loads_with_safe_defaults():
    legacy = Session(chat_id="legacy")
    for field in ("slot_meta", "hard_constraints", "soft_preferences",
                  "object_reactions", "liked_object_ids", "rejected_object_ids",
                  "shown_object_ids", "positive_preferences",
                  "negative_preferences", "contradictions"):
        assert hasattr(legacy, field), field
    assert legacy.misunderstanding_count == 0
    assert legacy.repair_mode is False


def test_legacy_session_json_without_wave3_fields_round_trips():
    legacy_payload = {
        "chat_id": "old",
        "asked_core": True,
        "lead": {"budget": 150000, "guests": 2, "districts": ["Раваи"]},
    }
    fields = Session.__dataclass_fields__
    lead = LeadProfile(**legacy_payload.pop("lead"))
    s = Session(**{k: v for k, v in legacy_payload.items() if k in fields})
    s.lead = lead
    # Wave 3 accessors must not raise on a session that predates them.
    assert slot_confidence(s, "budget") is Confidence.INFERRED
    assert compute_temperature(s) in set(LeadTemperature)
    assert build_handoff_note(s).startswith(HANDOFF_MARKER)
    assert build_active_request(s)["repair_mode"] is False


def test_backfill_does_not_invent_confirmed(session):
    session.lead = LeadProfile(budget=150000, guests=2)
    backfill_from_lead(session)
    assert slot_confidence(session, "budget") is Confidence.INFERRED
    assert slot_confidence(session, "guests") is Confidence.INFERRED


def test_session_stays_serialisable_after_a_wave3_turn():
    q = make_qualifier()
    s = Session(chat_id="ser", chosen=A)
    s.lead.preferred_object_id = "F_1"
    q.handle_message(s, "слишком дорого", {})
    payload = dataclasses.asdict(s)
    json.dumps(payload, ensure_ascii=False, default=str)


def test_correction_keeps_criteria_and_archives_owner_request():
    q = make_qualifier()
    s = Session(chat_id="keep", asked_core=True, awaiting_owner=True, chosen=A)
    s.lead = LeadProfile(check_in=date(2026, 9, 1), guests=2, budget=150000,
                         districts=["Раваи"], preferred_object_id="F_1")
    turn = q.handle_message(s, "не 150, а 200 тысяч", {})
    assert s.lead.budget == 200000
    assert s.lead.check_in == date(2026, 9, 1)
    assert s.lead.guests == 2
    assert s.lead.districts == ["Раваи"]
    # Old owner check is archived; the new criteria trigger a fresh one.
    assert [i["object_id"] for i in s.pending_owner_requests] == ["F_1"]
    assert turn.need_owner_check is True


def test_correction_without_dates_leaves_no_active_owner_wait():
    q = make_qualifier()
    s = Session(chat_id="keep2", awaiting_owner=True, chosen=A)
    s.lead = LeadProfile(budget=150000, preferred_object_id="F_1")
    q.handle_message(s, "не 150, а 200 тысяч", {})
    assert s.awaiting_owner is False
    assert [i["object_id"] for i in s.pending_owner_requests] == ["F_1"]
