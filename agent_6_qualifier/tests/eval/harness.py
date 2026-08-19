"""Eval harness for Agent6 qualification scenarios — not imported by production runtime."""
from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from agent6_qualifier.models import Availability, LeadProfile, Listing
from agent6_qualifier.qualifier import Qualifier, Session, Turn

EVALS_DIR = Path(__file__).resolve().parent
SCENARIOS_PATH = EVALS_DIR / "scenarios.yaml"

# Shared listing fixtures (object ids stable across scenarios)
CHOSEN = Listing(
    object_id="F_20260809_001",
    title="Вилла",
    district="Раваи",
    housing_type="вилла",
    rooms=2,
    price_month=50000,
    tg_post_url="https://t.me/OpenHome_th/10",
)
ALT = Listing(
    object_id="F_20260809_002",
    title="Вилла рядом",
    district="Раваи",
    housing_type="вилла",
    rooms=2,
    price_month=52000,
    tg_post_url="https://t.me/OpenHome_th/11",
    photos_url="https://r2.example/F_20260809_002/photos",
)
BUSY = Listing(
    object_id="F_20260809_003",
    title="Занятая вилла",
    district="Раваи",
    housing_type="вилла",
    rooms=2,
    price_month=50000,
    availability=Availability.BUSY,
    busy_until=date(2026, 8, 15),
)
FB_SHORT = Listing(
    object_id="F_20260801_001",
    title="FB вилла",
    district="Банг Тао",
    housing_type="вилла",
    rooms=3,
    price_month=80000,
    source_url="https://facebook.com/marketplace/item/123",
)
AIRBNB = Listing(
    object_id="A_20260810_001",
    title="Airbnb кондо",
    district="Ката",
    housing_type="кондо",
    rooms=1,
    price_month=35000,
    source_url="https://airbnb.com/rooms/123",
)

FIXTURES: dict[str, Listing] = {
    "CHOSEN": CHOSEN,
    "ALT": ALT,
    "BUSY": BUSY,
    "FB_SHORT": FB_SHORT,
    "AIRBNB": AIRBNB,
}


def default_listings() -> list[Listing]:
    return list(FIXTURES.values())


def make_qualifier(listings: list[Listing] | None = None) -> Qualifier:
    items = listings if listings is not None else default_listings()
    by_id = {l.object_id: l for l in items}
    return Qualifier(find_by_id=by_id.get, fetch_all=lambda: items)


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    return date.fromisoformat(str(val)[:10])


def build_session(spec: dict, chat_id: str = "eval") -> Session:
    lead_spec = dict(spec.get("lead") or {})
    for key in ("check_in", "check_out"):
        if lead_spec.get(key):
            lead_spec[key] = _parse_date(lead_spec[key])
    lead = LeadProfile(**{k: v for k, v in lead_spec.items() if k in LeadProfile.__dataclass_fields__})

    chosen_key = spec.get("chosen")
    chosen = FIXTURES.get(chosen_key) if isinstance(chosen_key, str) else None

    base = {
        "chat_id": spec.get("chat_id") or chat_id,
        "lead": lead,
        "chosen": chosen,
    }
    session_fields = Session.__dataclass_fields__
    for key, val in spec.items():
        if key in ("lead", "chosen", "chat_id"):
            continue
        if key in session_fields:
            base[key] = val

    # post-merge fields may not exist on refactor Session — setattr for forward compat
    s = Session(**{k: v for k, v in base.items() if k in session_fields})
    for key, val in spec.items():
        if key not in session_fields and key not in ("lead", "chosen", "chat_id"):
            setattr(s, key, val)
    return s


def run_scenario(spec: dict, qualifier: Qualifier | None = None) -> tuple[Session, list[Turn]]:
    q = qualifier or make_qualifier()
    session = build_session(spec.get("session") or {}, chat_id=spec.get("id", "eval"))
    turns: list[Turn] = []
    for step in spec.get("steps") or []:
        turn = q.handle_message(session, step.get("message") or "", step.get("update") or {})
        turns.append(turn)
    return session, turns


def _get_pending(session: Session, object_id: str) -> dict | None:
    pending = getattr(session, "pending_owner_requests", None) or []
    for item in pending:
        if item.get("object_id") == object_id:
            return item
    return None


def assert_step(session: Session, turn: Turn, assert_spec: dict, scenario_id: str) -> None:
    prefix = f"[{scenario_id}]"

    if "silent" in assert_spec:
        got = getattr(turn, "silent", False)
        assert got == assert_spec["silent"], f"{prefix} silent expected {assert_spec['silent']}, got {got}"

    reply = turn.reply_draft or ""
    for substr in assert_spec.get("reply_contains") or []:
        assert substr in reply, f"{prefix} reply missing '{substr}': {reply[:200]}"

    for substr in assert_spec.get("forbidden_reply_contains") or []:
        assert substr not in reply, f"{prefix} forbidden '{substr}' in reply: {reply[:200]}"

    if assert_spec.get("need_owner_check") is not None:
        assert turn.need_owner_check == assert_spec["need_owner_check"], (
            f"{prefix} need_owner_check"
        )

    if assert_spec.get("handoff_to_human") is not None:
        assert turn.handoff_to_human == assert_spec["handoff_to_human"], (
            f"{prefix} handoff_to_human"
        )

    sess_assert = dict(assert_spec.get("session") or {})
    if assert_spec.get("chosen_object_id"):
        oid = assert_spec["chosen_object_id"]
        actual_oid = session.chosen.object_id if session.chosen else ""
        assert actual_oid == oid, f"{prefix} chosen.object_id expected {oid}, got {actual_oid}"
    if assert_spec.get("chosen_is_null"):
        assert session.chosen is None, f"{prefix} expected chosen=None"
    if "chosen" in sess_assert:
        key_val = sess_assert.pop("chosen")
        if isinstance(key_val, str) and key_val in FIXTURES:
            assert session.chosen is FIXTURES[key_val], (
                f"{prefix} session.chosen expected fixture {key_val}"
            )
        else:
            assert session.chosen == key_val, f"{prefix} session.chosen mismatch"
    for key, expected in sess_assert.items():
        actual = getattr(session, key, None)
        assert actual == expected, f"{prefix} session.{key} expected {expected}, got {actual}"

    lead_assert = assert_spec.get("lead") or {}
    for key, expected in lead_assert.items():
        actual = getattr(session.lead, key, None)
        if key in ("check_in", "check_out") and expected:
            expected = _parse_date(expected)
        assert actual == expected, f"{prefix} lead.{key} expected {expected}, got {actual}"

    if assert_spec.get("pending_owner_object"):
        oid = assert_spec["pending_owner_object"]
        assert _get_pending(session, oid) is not None, f"{prefix} no pending for {oid}"

    if assert_spec.get("lead_preserved"):
        for key in assert_spec["lead_preserved"]:
            # value checked in scenario setup — only verify still set after turn
            val = getattr(session.lead, key, None)
            assert val is not None and val != "" and val != [], f"{prefix} lead.{key} not preserved"

    if assert_spec.get("wants_selection") is not None:
        assert session.wants_selection == assert_spec["wants_selection"], (
            f"{prefix} wants_selection"
        )

    # ---- Wave 3 assertions ----

    for slot, expected in (assert_spec.get("slot_confidence") or {}).items():
        from agent6_qualifier.qualification_meta import slot_confidence

        got = slot_confidence(session, slot).value
        assert got == expected, (
            f"{prefix} slot_confidence[{slot}] expected {expected}, got {got}"
        )

    if assert_spec.get("handoff_note_contains"):
        from agent6_qualifier.handoff_note import build_handoff_note

        note = build_handoff_note(session)
        for substr in assert_spec["handoff_note_contains"]:
            assert substr in note, f"{prefix} handoff note missing '{substr}': {note}"

    if assert_spec.get("lead_temperature"):
        from agent6_qualifier.lead_temperature import compute_temperature

        got = compute_temperature(session).value
        assert got == assert_spec["lead_temperature"], (
            f"{prefix} lead_temperature expected "
            f"{assert_spec['lead_temperature']}, got {got}"
        )

    if assert_spec.get("next_best_action"):
        from agent6_qualifier.active_request import compute_next_best_action

        got = compute_next_best_action(session).value
        assert got == assert_spec["next_best_action"], (
            f"{prefix} next_best_action expected "
            f"{assert_spec['next_best_action']}, got {got}"
        )

    for key in ("rejected_object_ids", "liked_object_ids", "shown_object_ids"):
        if assert_spec.get(key) is not None:
            got = list(getattr(session, key, None) or [])
            assert got == assert_spec[key], f"{prefix} {key} expected {assert_spec[key]}, got {got}"

    if assert_spec.get("rejected_not_offered"):
        for oid in assert_spec["rejected_not_offered"]:
            assert oid not in reply, f"{prefix} rejected {oid} offered again: {reply[:200]}"

    for entry in assert_spec.get("hard_constraints") or []:
        from agent6_qualifier.constraints import hard_constraints

        slots = {i.get("slot") for i in hard_constraints(session)}
        assert entry in slots, f"{prefix} expected HARD constraint on {entry}, got {slots}"

    for entry in assert_spec.get("soft_preferences") or []:
        from agent6_qualifier.constraints import soft_preferences

        slots = {i.get("slot") for i in soft_preferences(session)}
        assert entry in slots, f"{prefix} expected SOFT preference on {entry}, got {slots}"

    if assert_spec.get("negative_preference_kinds"):
        kinds = {i.get("kind") for i in (getattr(session, "negative_preferences", None) or [])}
        for kind in assert_spec["negative_preference_kinds"]:
            assert kind in kinds, f"{prefix} missing negative preference '{kind}', got {kinds}"

    if assert_spec.get("positive_preference_kinds"):
        kinds = {i.get("kind") for i in (getattr(session, "positive_preferences", None) or [])}
        for kind in assert_spec["positive_preference_kinds"]:
            assert kind in kinds, f"{prefix} missing positive preference '{kind}', got {kinds}"


def load_scenarios() -> list[dict]:
    raw = yaml.safe_load(SCENARIOS_PATH.read_text(encoding="utf-8"))
    return list(raw.get("scenarios") or [])


def scenarios_by_tier(tier: str) -> list[dict]:
    return [s for s in load_scenarios() if s.get("tier") == tier]


def run_and_assert(spec: dict, qualifier: Qualifier | None = None) -> None:
    """Run the scenario, checking each step's assertions right after that turn.

    Session state is asserted at the point the step describes, not at the end
    of the scenario — otherwise a later turn could mask a wrong intermediate
    state (and vice versa).
    """
    q = qualifier or make_qualifier()
    session = build_session(spec.get("session") or {}, chat_id=spec.get("id", "eval"))
    for step in spec.get("steps") or []:
        turn = q.handle_message(session, step.get("message") or "", step.get("update") or {})
        assert_spec = step.get("assert") or {}
        if assert_spec:
            assert_step(session, turn, assert_spec, spec.get("id", "?"))
