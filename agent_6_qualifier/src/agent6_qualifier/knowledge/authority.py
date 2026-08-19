"""CORE WINS: knowledge must never override deterministic business truth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent6_qualifier.knowledge.models import KnowledgeEntry
from agent6_qualifier.models import LeadProfile, Listing
from agent6_qualifier.rental_policy import FB_MIN_MONTHS, evaluate_rental_policy


@dataclass
class AuthorityDecision:
    ok: bool
    reason: str
    canonical_value: Any = None
    knowledge_ignored: bool = False


def rental_policy_wins(
    listing: Listing | None,
    lead: LeadProfile,
    *,
    knowledge_claim_min_months: float | None = None,
) -> AuthorityDecision:
    """Even if knowledge says FB min=1, code min (FB_MIN_MONTHS) wins."""
    result = evaluate_rental_policy(listing, lead)
    if knowledge_claim_min_months is not None and knowledge_claim_min_months != FB_MIN_MONTHS:
        return AuthorityDecision(
            ok=result.ok,
            reason=f"core_rental_policy_wins code_min={FB_MIN_MONTHS} knowledge_claim={knowledge_claim_min_months}",
            canonical_value=FB_MIN_MONTHS,
            knowledge_ignored=True,
        )
    return AuthorityDecision(
        ok=result.ok,
        reason=result.reason,
        canonical_value=FB_MIN_MONTHS if result.policy == "LONG_TERM_ONLY" else None,
        knowledge_ignored=False,
    )


def known_fields_cannot_become_missing(
    lead: LeadProfile,
    *,
    knowledge_says_missing: list[str] | None = None,
) -> AuthorityDecision:
    """Knowledge cannot force re-asking of already known session fields."""
    known = []
    if lead.check_in:
        known.append("check_in")
    if lead.stay_months is not None:
        known.append("stay_months")
    if lead.budget is not None:
        known.append("budget")
    if lead.guests:
        known.append("guests")
    if lead.bedrooms is not None:
        known.append("bedrooms")
    conflict = [f for f in (knowledge_says_missing or []) if f in known]
    if conflict:
        return AuthorityDecision(
            ok=False,
            reason=f"knowledge_cannot_unset_known:{','.join(conflict)}",
            canonical_value=known,
            knowledge_ignored=True,
        )
    return AuthorityDecision(ok=True, reason="no_conflict", canonical_value=known)


def ownership_blocks_bot(owner_state: str | None) -> AuthorityDecision:
    """HUMAN_HANDOFF / PAUSED / CLOSED cannot be bypassed by knowledge."""
    state = (owner_state or "BOT_ACTIVE").upper()
    if state in {"HUMAN_HANDOFF", "PAUSED", "CLOSED"}:
        return AuthorityDecision(
            ok=False,
            reason=f"ownership_blocks_bot:{state}",
            canonical_value=state,
            knowledge_ignored=True,
        )
    return AuthorityDecision(ok=True, reason="bot_may_reply", canonical_value=state)


def correlation_required(resolve_code: str | None) -> AuthorityDecision:
    code = (resolve_code or "").upper()
    if code in {"OWNER_REQUEST_AMBIGUOUS", "OWNER_REQUEST_NOT_FOUND", "AMBIGUOUS", "NOT_FOUND"}:
        return AuthorityDecision(
            ok=False,
            reason=f"correlation_blocks_guess:{code}",
            canonical_value=code,
            knowledge_ignored=True,
        )
    if code == "OWNER_REPLY_DUPLICATE":
        return AuthorityDecision(
            ok=False,
            reason="idempotency_blocks_duplicate",
            canonical_value=code,
            knowledge_ignored=True,
        )
    return AuthorityDecision(ok=True, reason="correlation_ok", canonical_value=code)


def role_cannot_flip_client_to_owner(
    current_role: str | None,
    *,
    knowledge_wants_role: str | None = None,
) -> AuthorityDecision:
    cur = (current_role or "").upper()
    want = (knowledge_wants_role or "").upper()
    if cur == "CLIENT" and want in {"OWNER", "AGENT"}:
        return AuthorityDecision(
            ok=False,
            reason="knowledge_cannot_flip_CLIENT_to_OWNER",
            canonical_value=cur,
            knowledge_ignored=True,
        )
    return AuthorityDecision(ok=True, reason="role_ok", canonical_value=cur)


def strip_hard_rule_overrides(entries: list[KnowledgeEntry]) -> list[KnowledgeEntry]:
    """Advisory layer only — HARD_RULE entries are references, not executable overrides."""
    # Nothing to strip for runtime: callers must use rental_policy / qualifier.
    # Kept as explicit API for tests / future guards.
    return list(entries)
