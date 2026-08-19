"""Retrieve compact relevant knowledge entries for a turn (fail-safe)."""

from __future__ import annotations

import logging
import re
from typing import Iterable

from agent6_qualifier.knowledge.index import load_index
from agent6_qualifier.knowledge.models import (
    KnowledgeEntry,
    KnowledgeStatus,
    RetrievalContext,
    RetrievalResult,
)

logger = logging.getLogger(__name__)

LIVE_STATUSES = {
    KnowledgeStatus.CURRENT.value,
    KnowledgeStatus.ADVISORY.value,
    KnowledgeStatus.APPROVED.value,
}

# Rejected / candidate / todo never enter live retrieval.
BLOCKED_STATUSES = {
    KnowledgeStatus.CANDIDATE.value,
    KnowledgeStatus.REJECTED.value,
    KnowledgeStatus.TODO.value,
}


def _normalize_agent(agent: str) -> str:
    a = (agent or "AGENT6").upper().replace(" ", "")
    if a in ("6", "QUALIFIER"):
        return "AGENT6"
    if a in ("7", "ENVOY"):
        return "AGENT7"
    if a.startswith("AGENT"):
        return a
    return "AGENT6"


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_]+", (text or "").lower()) if len(t) > 2}


class KnowledgeRetriever:
    def __init__(
        self,
        entries: list[KnowledgeEntry] | None = None,
        *,
        max_entries: int = 6,
        allow_section_entries: bool = False,
    ):
        self._entries = entries
        self.max_entries = max_entries
        self.allow_section_entries = allow_section_entries

    def _catalog(self) -> list[KnowledgeEntry]:
        if self._entries is not None:
            return self._entries
        try:
            return load_index()
        except Exception as exc:  # noqa: BLE001
            logger.warning("KNOWLEDGE_FALLBACK catalog err=%s", type(exc).__name__)
            return []

    def retrieve(self, ctx: RetrievalContext) -> RetrievalResult:
        try:
            return self._retrieve(ctx)
        except Exception as exc:  # noqa: BLE001
            logger.warning("KNOWLEDGE_FALLBACK retrieve err=%s", type(exc).__name__)
            return RetrievalResult(fallback=True, reason=f"retrieve_error:{type(exc).__name__}")

    def _retrieve(self, ctx: RetrievalContext) -> RetrievalResult:
        catalog = self._catalog()
        if not catalog:
            return RetrievalResult(fallback=True, reason="empty_catalog")

        agent = _normalize_agent(ctx.agent)
        wanted_tags = set(t.lower() for t in (ctx.extra_tags or []))
        for key in (
            ctx.object_source,
            ctx.rental_policy,
            ctx.objection,
            ctx.situation,
            ctx.intent,
            ctx.stage,
        ):
            if key:
                wanted_tags.add(str(key).lower().replace(" ", "_"))
        if ctx.long_term is True:
            wanted_tags.update({"long_term", "long-term", "facebook"})
        if ctx.current_object:
            wanted_tags.add("specific_property")
            if str(ctx.current_object).upper().startswith("F_"):
                wanted_tags.update({"facebook", "long_term"})
            if str(ctx.current_object).upper().startswith("A_"):
                wanted_tags.add("airbnb")
        msg_tokens = _token_set(ctx.message_text)
        # Intent heuristics from free text
        low = (ctx.message_text or "").lower()
        if any(x in low for x in ("дорог", "дорого", "price", "дешев", "бюджет нет", "без бюджета")):
            wanted_tags.add("price")
            wanted_tags.add("objection")
        if "airbnb" in low and any(x in low for x in ("дешев", "дешевле", "cheaper")):
            wanted_tags.add("airbnb_comparison")
        if any(x in low for x in ("подумаю", "later", "think")):
            wanted_tags.add("think_later")
        if any(x in low for x in ("вариант", "пришлите", "покажите", "options")):
            wanted_tags.add("send_options")
        if any(x in low for x in ("3 месяц", "на месяц", "на 3", "two month", "3 months")):
            wanted_tags.add("short_stay")
        if any(x in low for x in ("год", "12 мес", "long", "полгода", "6 мес")):
            wanted_tags.add("long_term")

        scored: list[tuple[int, KnowledgeEntry]] = []
        for e in catalog:
            if e.status in BLOCKED_STATUSES:
                continue
            if e.status not in LIVE_STATUSES and e.type != "SECTION":
                continue
            if e.type == "SECTION" and not self.allow_section_entries:
                continue
            if not e.applies_to(agent):
                continue
            # Strict file isolation: AGENT6 never gets AGENT7-only entries and vice versa.
            # BOTH / shared remain available to both.
            entry_agents = {a.upper() for a in (e.agents or [])}
            if entry_agents and "BOTH" not in entry_agents and agent not in entry_agents:
                continue

            score = int(e.priority or 0)
            etags = set(e.tags or [])
            overlap = wanted_tags & etags
            score += 25 * len(overlap)

            # Title / content soft match
            blob_tokens = _token_set(e.title) | set(e.tags or [])
            score += 3 * len(msg_tokens & blob_tokens)

            # Strong boosts for known situations
            if ctx.objection and ctx.objection.lower() in etags:
                score += 40
            if ctx.object_source and ctx.object_source.lower() in etags:
                score += 35
            if ctx.situation and ctx.situation.lower() in etags:
                score += 35
            if "hard" in (e.type or "").lower() and overlap:
                score += 15

            if score <= int(e.priority or 0) and not overlap:
                # Keep high-priority style/safety lightly available for first contact
                if e.type in {"STYLE", "SAFETY"} and ctx.intent in {
                    "first_contact",
                    "generic_inquiry",
                    "owner_first_message",
                }:
                    score += 5
                else:
                    continue
            scored.append((score, e))

        scored.sort(key=lambda x: (-x[0], -x[1].priority, x[1].id))
        picked: list[KnowledgeEntry] = []
        seen: set[str] = set()
        for _score, e in scored:
            if e.id in seen:
                continue
            picked.append(e)
            seen.add(e.id)
            if len(picked) >= self.max_entries:
                break

        return RetrievalResult(
            entries=picked,
            selected_ids=[e.display_id for e in picked],
            fallback=False,
            reason="ok" if picked else "no_match",
        )


def retrieve_for_turn(
    ctx: RetrievalContext,
    *,
    entries: list[KnowledgeEntry] | None = None,
    max_entries: int = 6,
) -> RetrievalResult:
    return KnowledgeRetriever(entries=entries, max_entries=max_entries).retrieve(ctx)


def filter_live_only(entries: Iterable[KnowledgeEntry]) -> list[KnowledgeEntry]:
    return [
        e
        for e in entries
        if e.status in LIVE_STATUSES and e.status not in BLOCKED_STATUSES
    ]
