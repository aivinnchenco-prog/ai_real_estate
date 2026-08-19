"""Rental duration policy layered on top of archive Agent6 qualification.

FACEBOOK MARKETPLACE listings: LONG_TERM_ONLY (minimum 6 months).
AIRBNB / other: FLEXIBLE (no extra gate here).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent6_qualifier.models import LeadProfile, Listing

FB_MIN_MONTHS = 6.0


@dataclass(frozen=True)
class RentalPolicyResult:
    source: str  # facebook | airbnb | unknown
    policy: str  # LONG_TERM_ONLY | FLEXIBLE
    ok: bool
    needs_duration_clarification: bool = False
    prompt: str = ""
    reason: str = ""


def listing_source_kind(listing: Listing | None) -> str:
    if listing is None:
        return "unknown"
    src = (listing.source_url or "").lower()
    if "facebook." in src or "fb.com" in src or "marketplace" in src:
        return "facebook"
    if listing.source_is_airbnb or "airbnb." in src:
        return "airbnb"
    # object id prefix heuristic used by parsers
    oid = (listing.object_id or "").upper()
    if oid.startswith("F_"):
        return "facebook"
    if oid.startswith("A_"):
        return "airbnb"
    return "unknown"


def evaluate_rental_policy(
    listing: Listing | None, lead: LeadProfile
) -> RentalPolicyResult:
    kind = listing_source_kind(listing)
    if kind == "facebook":
        months = lead.stay_months
        if months is not None and float(months) >= FB_MIN_MONTHS:
            return RentalPolicyResult(
                source=kind,
                policy="LONG_TERM_ONLY",
                ok=True,
                reason=f"stay_months={months}>={FB_MIN_MONTHS}",
            )
        # No checkout and no stay_months: archive treats missing checkout as year
        # contract — do not block; only block explicit short stays.
        if months is not None and float(months) < FB_MIN_MONTHS:
            return RentalPolicyResult(
                source=kind,
                policy="LONG_TERM_ONLY",
                ok=False,
                needs_duration_clarification=True,
                prompt=(
                    "По объектам с Facebook Marketplace аренда от 6 месяцев. "
                    "Подтвердите, пожалуйста, срок проживания (минимум 6 месяцев)."
                ),
                reason=f"stay_months={months}<{FB_MIN_MONTHS}",
            )
        return RentalPolicyResult(
            source=kind,
            policy="LONG_TERM_ONLY",
            ok=True,
            reason="no_explicit_short_stay",
        )
    if kind == "airbnb":
        return RentalPolicyResult(
            source=kind, policy="FLEXIBLE", ok=True, reason="airbnb_flexible"
        )
    return RentalPolicyResult(
        source=kind, policy="FLEXIBLE", ok=True, reason="unknown_flexible"
    )
