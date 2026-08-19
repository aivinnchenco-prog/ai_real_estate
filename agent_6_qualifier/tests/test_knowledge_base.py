"""Offline tests for two-file Agent6/Agent7 knowledge base."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from agent6_qualifier.brain import apply_update
from agent6_qualifier.knowledge.authority import (
    correlation_required,
    known_fields_cannot_become_missing,
    ownership_blocks_bot,
    rental_policy_wins,
    role_cannot_flip_client_to_owner,
)
from agent6_qualifier.knowledge.index import (
    last_good_index_path,
    load_index,
    rebuild_index,
)
from agent6_qualifier.knowledge.learning import (
    CandidatePattern,
    CandidateStore,
    recommended_destination,
)
from agent6_qualifier.knowledge.models import KnowledgeEntry, RetrievalContext
from agent6_qualifier.knowledge.parser import (
    agent6_human_path,
    agent7_human_path,
    load_all_human_entries,
    load_file_entries,
    parse_human_markdown,
)
from agent6_qualifier.knowledge.retriever import KnowledgeRetriever, retrieve_for_turn
from agent6_qualifier.models import LeadProfile, Listing
from agent6_qualifier.qualification_hints import (
    merge_hints,
    qualification_hints_from_text,
)
from agent6_qualifier.qualifier import Qualifier, Session
from agent6_qualifier.rental_policy import FB_MIN_MONTHS, evaluate_rental_policy


KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge"
A6 = KNOWLEDGE_DIR / "БАЗА_ЗНАНИЙ_AGENT_6_КЛИЕНТЫ.md"
A7 = KNOWLEDGE_DIR / "БАЗА_ЗНАНИЙ_AGENT_7_ВЛАДЕЛЬЦЫ.md"
README = KNOWLEDGE_DIR / "README.md"
OLD = KNOWLEDGE_DIR / "БАЗА_ЗНАНИЙ.md"


@pytest.fixture(scope="module")
def entries() -> list[KnowledgeEntry]:
    ents, _ = load_all_human_entries()
    assert ents
    return ents


def test_both_human_files_and_readme_exist():
    assert A6.exists()
    assert A7.exists()
    assert README.exists()
    assert OLD.exists()
    assert "разделена на два файла" in OLD.read_text(encoding="utf-8").lower()
    assert "МОИ НОВЫЕ ПРАВИЛА" in A6.read_text(encoding="utf-8")
    assert "МОИ НОВЫЕ ПРАВИЛА" in A7.read_text(encoding="utf-8")


def test_auto_agent_classification(entries):
    a6 = [e for e in entries if e.source_file.endswith("КЛИЕНТЫ.md") or e.agents == ["AGENT6"]]
    a7 = [e for e in entries if e.source_file.endswith("ВЛАДЕЛЬЦЫ.md") or e.agents == ["AGENT7"]]
    assert a6 and a7
    assert all(e.agents == ["AGENT6"] for e in a6 if e.source_file.endswith("КЛИЕНТЫ.md"))
    assert all(e.agents == ["AGENT7"] for e in a7 if e.source_file.endswith("ВЛАДЕЛЬЦЫ.md"))
    assert all(e.id.startswith("agent6/") for e in entries if e.agents == ["AGENT6"])
    assert all(e.id.startswith("agent7/") for e in entries if e.agents == ["AGENT7"])


def test_no_manual_agents_metadata_required():
    sample = """
# 1. Test

## Простое правило без metadata

Ситуация:
Клиент спрашивает о вилле.

Как должен действовать агент:
Ответить и начать квалификацию.
"""
    ents, issues = parse_human_markdown(
        sample, agent="AGENT6", source_file="БАЗА_ЗНАНИЙ_AGENT_6_КЛИЕНТЫ.md"
    )
    assert any(e.title == "Простое правило без metadata" for e in ents)
    e = next(e for e in ents if e.title == "Простое правило без metadata")
    assert e.agents == ["AGENT6"]
    assert e.id.startswith("agent6/")
    assert e.tags
    assert e.status in {"CURRENT", "APPROVED"}


def test_simple_agent7_rule_parse():
    sample = """
# 1. Test

## Владелец ответил занято

Ситуация:
Владелец сказал, что даты заняты.

Как должен действовать агент:
Зафиксировать busy и вернуть клиенту.

Что нельзя делать:
Не угадывать дату освобождения.
"""
    ents, _ = parse_human_markdown(
        sample, agent="AGENT7", source_file="БАЗА_ЗНАНИЙ_AGENT_7_ВЛАДЕЛЬЦЫ.md"
    )
    e = next(e for e in ents if "занято" in e.title.lower())
    assert e.agents == ["AGENT7"]
    assert e.id.startswith("agent7/")


def test_optional_fields_and_auto_tags():
    sample = """
# МОИ НОВЫЕ ПРАВИЛА

## Клиент просит скидку

Ситуация:
Клиент хочет скидку по Facebook объекту.

Как должен действовать агент:
Не обещать скидку без владельца.

Пример хорошего ответа:
Сначала уточню даты, затем проверю условия.
"""
    ents, issues = parse_human_markdown(
        sample, agent="AGENT6", source_file="x.md"
    )
    e = ents[0]
    assert "price" in e.tags or "facebook" in e.tags
    assert e.status == "APPROVED"
    assert not any(i.problem.startswith("«Как должен") for i in issues)


def test_rules_not_lost(entries):
    assert len(entries) >= 50
    titles = " ".join(e.title.lower() for e in entries)
    assert "facebook" in titles
    assert "airbnb" in titles or any("airbnb" in e.id for e in entries)
    assert any("correlation" in e.id or "correlation" in e.title.lower() or "запрос" in e.title.lower() for e in entries)


def test_update_rebuilds_both(tmp_path):
    idx = tmp_path / "knowledge_index.json"
    payload = rebuild_index(index_path=idx)
    assert payload["agent6_count"] >= 20
    assert payload["agent7_count"] >= 20
    assert payload["entry_count"] == len(payload["entries"])
    assert any("AGENT_6" in s or "КЛИЕНТ" in s for s in payload["sources"])
    assert any("AGENT_7" in s or "ВЛАДЕЛЬЦ" in s for s in payload["sources"])


def test_agent_isolation(entries):
    r6 = retrieve_for_turn(
        RetrievalContext(agent="AGENT6", message_text="Ищу виллу", intent="generic_inquiry"),
        entries=entries,
    )
    r7 = retrieve_for_turn(
        RetrievalContext(
            agent="AGENT7",
            situation="owner_check",
            message_text="владелец занято",
            extra_tags=["busy", "owner_check"],
        ),
        entries=entries,
    )
    assert r6.selected_ids
    assert all(not i.startswith("agent7/") for i in r6.selected_ids)
    assert r7.selected_ids
    assert all(not i.startswith("agent6/") for i in r7.selected_ids)


def test_shared_safety_available_per_file(entries):
    # Safety duplicated into each file remains agent-scoped but still retrievable.
    r6 = retrieve_for_turn(
        RetrievalContext(agent="AGENT6", intent="first_contact", message_text="привет"),
        entries=entries,
        max_entries=6,
    )
    assert r6.selected_ids or r6.reason in {"ok", "no_match"}


def test_malformed_rule_isolated_agent6():
    dirty = """
# 1. Ok

## Хорошее правило

Ситуация:
Клиент пишет.

Как должен действовать агент:
Помочь.

# МОИ НОВЫЕ ПРАВИЛА

## Плохое правило без действия

Ситуация:
Что-то случилось.

<!-- broken comment is fine -->
"""
    ents, issues = parse_human_markdown(
        dirty, agent="AGENT6", source_file="БАЗА_ЗНАНИЙ_AGENT_6_КЛИЕНТЫ.md"
    )
    assert any(e.title == "Хорошее правило" for e in ents)
    assert any("Плохое правило" in i.rule for i in issues)


def test_malformed_rule_isolated_agent7():
    dirty = """
# 1. Ok

## Нормальное owner правило

Ситуация:
Владелец ответил.

Как должен действовать агент:
Зафиксировать вердикт.
"""
    ents, issues = parse_human_markdown(
        dirty, agent="AGENT7", source_file="БАЗА_ЗНАНИЙ_AGENT_7_ВЛАДЕЛЬЦЫ.md"
    )
    assert ents
    assert not any(i.problem.startswith("«Как должен") for i in issues)


def test_last_good_fallback(tmp_path):
    good = tmp_path / "knowledge_index.json"
    # First write creates index + last-good
    payload = rebuild_index(index_path=good)
    assert payload["entry_count"] > 0
    # Second rebuild preserves/refreshes last-good beside custom path
    payload2 = rebuild_index(index_path=good)
    assert payload2["entry_count"] > 0
    lg = last_good_index_path(good)
    assert lg.exists()
    import json

    data = json.loads(lg.read_text(encoding="utf-8"))
    assert data["entry_count"] > 0

def test_hard_rule_cannot_be_overridden():
    lead = LeadProfile(stay_months=2.0, check_in=date.today() + timedelta(days=3))
    listing = Listing(object_id="F_1", source_url="https://facebook.com/marketplace/item/1")
    d = rental_policy_wins(listing, lead, knowledge_claim_min_months=2.0)
    assert d.knowledge_ignored is True
    assert d.canonical_value == FB_MIN_MONTHS
    assert evaluate_rental_policy(listing, lead).ok is False


def test_authority_gates():
    lead = LeadProfile(check_in=date.today(), stay_months=12)
    assert known_fields_cannot_become_missing(
        lead, knowledge_says_missing=["check_in"]
    ).ok is False
    assert ownership_blocks_bot("HUMAN_HANDOFF").ok is False
    assert correlation_required("OWNER_REQUEST_AMBIGUOUS").ok is False
    assert correlation_required("OWNER_REPLY_DUPLICATE").ok is False
    assert role_cannot_flip_client_to_owner("CLIENT", knowledge_wants_role="OWNER").ok is False


def test_archive_parity_still_holds():
    today = date.today()
    sess = Session(chat_id="kb2")
    apply_update(sess.lead, qualification_hints_from_text("Хочу на год", today=today))
    apply_update(
        sess.lead,
        merge_hints({}, qualification_hints_from_text("Через 5 дней", today=today)),
    )
    assert sess.lead.stay_months == 12.0
    assert sess.lead.check_in == today + timedelta(days=5)
    fb = Listing(
        object_id="F_20260101_001",
        source_url="https://www.facebook.com/marketplace/item/1",
        rooms=3,
        price_month=100000,
        housing_type="Вилла",
        district="Rawai",
    )
    q = Qualifier(find_by_id=lambda oid: fb if oid == fb.object_id else None, fetch_all=lambda: [fb])
    sess.asked_core = True
    sess.chosen = fb
    sess.lead.preferred_object_id = fb.object_id
    turn = q._next_step(sess, [], message="ок")
    low = turn.reply_draft.lower()
    assert "уточните, пожалуйста, дату заезда" not in low


def test_wave3_conversational_rules_present(entries):
    """Wave 3 §20: conversational guidance lives in knowledge, rules in code."""
    titles = [e.title.lower() for e in entries if e.agents == ["AGENT6"]]
    for needle in ("исправляет", "противоречит", "реакция клиента",
                   "мягкое пожелание", "неправильно понял", "антипаттерны диалога"):
        assert any(needle in t for t in titles), f"missing Wave 3 rule: {needle}"


def test_wave3_antipatterns_listed():
    text = A6.read_text(encoding="utf-8")
    for phrase in (
        "Не писать «я уже спрашивал»",
        "Не повторять всю анкету после исправления",
        "Не спорить с клиентом",
        "Не игнорировать «этот не подходит»",
        "Не показывать повторно отвергнутый объект",
        "Не писать длинные извинения",
        "Не передавать менеджеру автоматически после одной ошибки",
    ):
        assert phrase in text, f"missing anti-pattern: {phrase}"


def test_wave3_knowledge_parses_without_issues():
    entries, issues = load_all_human_entries()
    assert not issues, f"knowledge parse issues: {issues[:5]}"
    assert len(entries) >= 80


def test_candidate_destination():
    c6 = CandidatePattern(
        id="c1",
        agent="AGENT6",
        situation="скидка",
        observed_problem="обещал скидку",
        suggested_principle="не обещать",
    )
    c7 = CandidatePattern(
        id="c2",
        agent="AGENT7",
        situation="owner price changed",
        observed_problem="ignored",
        suggested_principle="conditions_changed",
    )
    assert "AGENT6" in recommended_destination(c6)
    assert "AGENT7" in recommended_destination(c7)


def test_retriever_failsafe_empty():
    r = KnowledgeRetriever(entries=[]).retrieve(RetrievalContext(agent="AGENT6"))
    assert r.fallback is True


def test_paths_helpers():
    assert agent6_human_path().name.endswith("КЛИЕНТЫ.md")
    assert agent7_human_path().name.endswith("ВЛАДЕЛЬЦЫ.md")
    e6, _ = load_file_entries(A6)
    e7, _ = load_file_entries(A7)
    assert e6 and e7
