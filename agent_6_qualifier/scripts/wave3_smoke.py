#!/usr/bin/env python3
"""Wave 3 safe smoke: 8 mock dialogues against the local qualifier.

Offline by construction — in-memory sessions, fixture listings, no amoCRM,
Wazzup or Telegram traffic and no messages to real clients. Safe to run on
production to verify the deployed build.

    python3 scripts/wave3_smoke.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent))

# Keep the run deterministic: no LLM fallback, no outbound calls.
for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(key, None)
os.environ.setdefault("AGENT6_MAX_REPAIR_ATTEMPTS", "2")

from agent6_qualifier.active_request import compute_next_best_action  # noqa: E402
from agent6_qualifier.handoff_note import build_handoff_note  # noqa: E402
from agent6_qualifier.lead_temperature import compute_temperature  # noqa: E402
from agent6_qualifier.models import LeadProfile, Listing  # noqa: E402
from agent6_qualifier.qualifier import Qualifier, Session  # noqa: E402
from agent6_qualifier.reactions import mark_shown  # noqa: E402


def _listing(oid: str, **kw) -> Listing:
    base = dict(object_id=oid, title="Вилла", district="Раваи",
                housing_type="вилла", rooms=2, price_month=50000)
    base.update(kw)
    return Listing(**base)


NEAR = _listing("F_1")
ALSO_NEAR = _listing("F_2", price_month=52000)
FAR = _listing("F_3", district="Банг Тао", price_month=90000)
LISTINGS = [NEAR, ALSO_NEAR, FAR]

TOMORROW = date.today() + timedelta(days=30)


def qualifier() -> Qualifier:
    by_id = {l.object_id: l for l in LISTINGS}
    return Qualifier(find_by_id=by_id.get, fetch_all=lambda: LISTINGS)


class Result:
    def __init__(self, name: str) -> None:
        self.name = name
        self.checks: list[tuple[str, bool, str]] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.checks.append((label, bool(ok), detail))

    @property
    def ok(self) -> bool:
        return all(c[1] for c in self.checks)


def case1() -> Result:
    """budget 150k → «не 150, а 200» → only budget changes."""
    r = Result("CASE 1 correction: only budget updates")
    q, s = qualifier(), Session(chat_id="smoke1", asked_core=True, wants_selection=True)
    s.lead = LeadProfile(budget=150000, districts=["Раваи"], guests=2, check_in=TOMORROW)
    q.handle_message(s, "не 150, а 200 тысяч", {})
    r.check("budget = 200000", s.lead.budget == 200000, str(s.lead.budget))
    r.check("districts kept", s.lead.districts == ["Раваи"], str(s.lead.districts))
    r.check("guests kept", s.lead.guests == 2, str(s.lead.guests))
    r.check("check_in kept", s.lead.check_in == TOMORROW, str(s.lead.check_in))
    r.check("budget CONFIRMED",
            s.slot_meta.get("budget", {}).get("confidence") == "CONFIRMED",
            str(s.slot_meta.get("budget")))
    return r


def case2() -> Result:
    """«этот слишком далеко» → object rejected + negative location signal."""
    r = Result("CASE 2 reaction: rejected + location signal")
    q, s = qualifier(), Session(chat_id="smoke2", chosen=NEAR)
    s.lead = LeadProfile(preferred_object_id="F_1", check_in=TOMORROW, guests=2)
    turn = q.handle_message(s, "этот слишком далеко", {})
    r.check("F_1 rejected", "F_1" in s.rejected_object_ids, str(s.rejected_object_ids))
    kinds = {p.get("kind") for p in s.negative_preferences}
    r.check("location signal", "location" in kinds, str(kinds))
    r.check("reply is not defensive",
            "извин" not in (turn.reply_draft or "").lower(), turn.reply_draft or "")
    return r


def case3() -> Result:
    """next shortlist must not repeat a rejected object."""
    r = Result("CASE 3 rejected object not shown again")
    q, s = qualifier(), Session(chat_id="smoke3", asked_core=True, wants_selection=True,
                                offered_alternatives=True)
    s.lead = LeadProfile(check_in=TOMORROW, guests=2, districts=["Раваи"])
    mark_shown(s, ["F_1"])
    q.handle_message(s, "этот не подходит", {})
    turn = q.handle_message(s, "покажите ещё варианты", {"wants_alternatives": True})
    reply = turn.reply_draft or ""
    r.check("F_1 rejected", "F_1" in s.rejected_object_ids, str(s.rejected_object_ids))
    r.check("F_1 not re-offered", "F_1" not in reply, reply[:120])
    return r


def case4() -> Result:
    """«хочу современнее» → soft style preference, not a hard filter."""
    r = Result("CASE 4 soft style preference")
    q, s = qualifier(), Session(chat_id="smoke4", asked_core=True, wants_selection=True)
    s.lead = LeadProfile(check_in=TOMORROW, guests=2, districts=["Раваи"])
    q.handle_message(s, "хочу современнее", {})
    kinds = {p.get("kind") for p in s.positive_preferences}
    r.check("style preference stored", "style" in kinds, str(kinds))
    r.check("not promoted to hard constraint",
            not [i for i in s.hard_constraints if i.get("slot") == "style"],
            str(s.hard_constraints))
    return r


def case5() -> Result:
    """conflicting stay without a clear correction → one narrow question."""
    r = Result("CASE 5 contradiction → narrow clarification")
    q, s = qualifier(), Session(chat_id="smoke5", asked_core=True, wants_selection=True)
    s.lead = LeadProfile(check_in=TOMORROW, stay_months=12, guests=2)
    turn = q.handle_message(s, "до декабря", {"check_out": date(TOMORROW.year, 12, 1)})
    reply = turn.reply_draft or ""
    r.check("clarifies the stay", "срок изменился" in reply, reply[:160])
    r.check("exactly one question", reply.count("?") == 1, reply[:160])
    r.check("no re-questionnaire",
            "Бюджет в месяц" not in reply and "Сколько спален" not in reply, reply[:160])
    return r


def case6() -> Result:
    """«ты понимаешь? я хочу новый дом» → repair + new search, no stale wait."""
    r = Result("CASE 6 repair + new search")
    q, s = qualifier(), Session(chat_id="smoke6", awaiting_owner=True, chosen=NEAR)
    s.lead = LeadProfile(preferred_object_id="F_1", check_in=TOMORROW, guests=2)
    turn = q.handle_message(s, "ты понимаешь? я хочу новый дом", {})
    reply = (turn.reply_draft or "").lower()
    r.check("repair mode on", s.repair_mode is True, str(s.repair_mode))
    r.check("no stale owner-wait template", "жду ответ" not in reply, reply[:160])
    r.check("no handoff on first misunderstanding",
            turn.handoff_to_human is False, str(turn.handoff_to_human))
    r.check("at most one question", (turn.reply_draft or "").count("?") <= 1, reply[:160])
    return r


def case7() -> Result:
    """repeated misunderstanding → threshold → handoff with a structured note."""
    r = Result("CASE 7 repair threshold → handoff")
    q, s = qualifier(), Session(chat_id="smoke7", wants_selection=True)
    first = q.handle_message(s, "ты не понял", {})
    second = q.handle_message(s, "я уже сказал", {})
    third = q.handle_message(s, "я не это имею в виду", {})
    r.check("no handoff on #1", first.handoff_to_human is False)
    r.check("no handoff on #2", second.handoff_to_human is False)
    r.check("handoff on #3", third.handoff_to_human is True)
    note = build_handoff_note(s, reason="REPAIR_THRESHOLD_EXCEEDED")
    r.check("structured note", note.startswith("[HANDOFF]"), note[:60])
    for section in ("Intent:", "Active request:", "Hard constraints:",
                    "Soft preferences:", "Shown objects:", "Liked:", "Rejected:",
                    "Pending owner checks:", "Lead temperature:",
                    "Reason for handoff:", "Next best action:",
                    "Last client message:"):
        r.check(f"note has {section}", section in note)
    return r


def case8() -> Result:
    """HOT lead signals → temperature HOT + sensible next action."""
    r = Result("CASE 8 hot lead temperature")
    q, s = qualifier(), Session(chat_id="smoke8", chosen=NEAR, awaiting_owner=True)
    s.lead = LeadProfile(preferred_object_id="F_1", check_in=TOMORROW, guests=2,
                         budget=90000)
    turn = q.handle_message(s, "что там по владельцу?", {})
    temperature = compute_temperature(s).value
    action = compute_next_best_action(s).value
    r.check("temperature HOT", temperature == "HOT", temperature)
    r.check("next action WAIT_OWNER", action == "WAIT_OWNER", action)
    r.check("temperature hidden from client",
            all(x not in (turn.reply_draft or "") for x in ("HOT", "WARM", "COLD")),
            turn.reply_draft or "")

    cold = Session(chat_id="smoke8c")
    q.handle_message(cold, "здравствуйте, интересует аренда", {})
    r.check("fresh lead is COLD", compute_temperature(cold).value == "COLD",
            compute_temperature(cold).value)
    return r


CASES = [case1, case2, case3, case4, case5, case6, case7, case8]


def main() -> int:
    results = []
    for case in CASES:
        try:
            results.append(case())
        except Exception as exc:  # noqa: BLE001 — smoke must report, not crash
            failed = Result(f"{case.__name__} raised")
            failed.check("no exception", False, f"{type(exc).__name__}: {exc}")
            results.append(failed)

    for res in results:
        print(f"{'PASS' if res.ok else 'FAIL'}  {res.name}")
        for label, ok, detail in res.checks:
            if not ok:
                print(f"        ✗ {label} — {detail}")

    failures = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failures)}/{len(results)} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
