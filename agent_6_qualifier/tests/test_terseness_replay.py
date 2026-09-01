"""A0/A5: outbound counts on A102/A103 replay + no consecutive duplicate replies."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.models import Listing
from agent6_qualifier.outbound_dedup import should_suppress_outbound
from agent6_qualifier.qualifier import Qualifier, Session

DOCS = Path(__file__).resolve().parents[1] / "docs" / "A102_A103_replay.json"

# Forensic dump: one outbound per inbound bubble (no WA debounce).
HISTORICAL_A103 = 8
HISTORICAL_A102 = 5


def _pool() -> list[Listing]:
    return [
        Listing(object_id="RAWAI_OK", title="Rawai villa", district="Rawai",
                price_month=45000, rooms=2, deposit=15000),
        Listing(object_id="KATA_OK", title="Kata villa", district="Kata",
                price_month=40000, rooms=2),
    ]


def _replay(key: str) -> list[str]:
    data = json.loads(DOCS.read_text(encoding="utf-8"))
    q = Qualifier(
        find_by_id={l.object_id: l for l in _pool()}.get,
        fetch_all=lambda: _pool(),
    )
    session = Session(chat_id=key)
    out: list[str] = []
    for turn in data[key]:
        inbound = turn.get("inbound") or ""
        update = dict(turn.get("gemini_raw") or {})
        result = q.handle_message(session, inbound, update)
        if result.silent or not (result.reply_draft or "").strip():
            continue
        if should_suppress_outbound(session, result.reply_draft):
            continue
        out.append(result.reply_draft)
        session.history.append({"role": "user", "text": inbound})
        session.history.append({"role": "assistant", "text": result.reply_draft})
    return out


def test_a0_historical_dump_outbound_counts():
    data = json.loads(DOCS.read_text(encoding="utf-8"))
    a103 = [t for t in data["A103_linusikdrm"] if t.get("final_client_text")]
    a102 = [t for t in data["A102_gennady"] if t.get("final_client_text")]
    assert len(a103) == HISTORICAL_A103
    assert len(a102) == HISTORICAL_A102


def test_a0_replay_outbound_drops_and_no_consecutive_dupes():
    a103 = _replay("A103_linusikdrm")
    a102 = _replay("A102_gennady")
    assert len(a103) < HISTORICAL_A103
    assert len(a102) < HISTORICAL_A102
    for seq in (a103, a102):
        for prev, cur in zip(seq, seq[1:]):
            assert prev.strip() != cur.strip()


def test_repair_repeated_bot_reply_does_not_add_recap():
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    session = Session(chat_id="rep")
    same = "Подскажите, пожалуйста, дату заезда."
    session.history = [
        {"role": "assistant", "text": same},
        {"role": "assistant", "text": same},
    ]
    turn = q.handle_message(session, "хм", {})
    assert "зафиксируем запрос заново" not in (turn.reply_draft or "")
    if turn.silent:
        return
    assert turn.reply_draft.count("Подскажите, пожалуйста, дату заезда.") <= 1


def test_open_search_asks_critical_slots_together():
    q = Qualifier(find_by_id=lambda _: None, fetch_all=lambda: [])
    session = Session(chat_id="mvc")
    turn = q.handle_message(session, "Ищу виллу, подберите варианты", {})
    text = turn.reply_draft.lower()
    assert "заезд" in text
    assert "человек" in text or "гост" in text
    assert "район" in text
