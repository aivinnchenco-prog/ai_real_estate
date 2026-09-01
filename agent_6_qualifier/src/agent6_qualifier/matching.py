"""Подбор альтернативных объектов по квалификации клиента.

Правила (см. AGENT_SPEC.md, раздел 5):
1. Объект не «занято» (или занят, но освобождается до заезда клиента).
2. Бюджет ±10% (настраивается; при пустой выдаче — уточнить допуск у клиента).
3. Район клиента, если задан.
4. Вместимость >= гостей (прокси — комнаты, пока нет отдельного поля).
5. Питомцы: pets=да → подходят «можно» И объекты с пустым полем (не указано != нельзя).
6. Похожесть на выбранный объект: тип жилья, комнаты ±1 — влияет на сортировку, не отсекает.
"""
from __future__ import annotations

from .districts import canonicalize_district, normalize_key
from .models import Availability, LeadProfile, Listing

# Hide listings whose «Цена за месяц» is a parser artefact (kWh / deposit)
# and which have no monthly chips from Agent 1/2.
PRICE_FLOOR = 3000.0


def _has_monthly_chips(listing: Listing) -> bool:
    monthly = listing.monthly_prices or {}
    if isinstance(monthly, dict):
        return any((entry or {}).get("price") for entry in monthly.values())
    return bool(monthly)


def price_sane(listing: Listing) -> bool:
    """Hard filter: drop missing / absurd monthly prices without chips."""
    price = listing.price_month
    chips = _has_monthly_chips(listing)
    if price is None:
        return chips
    if price < PRICE_FLOOR and not chips:
        return False
    return True


def budget_ok(price: float | None, lead: LeadProfile) -> bool:
    if price is None or lead.budget is None:
        return True  # нет данных — не отсекаем, уточним в диалоге
    tol = lead.budget * lead.budget_tolerance_pct / 100.0
    return price <= lead.budget + tol


def pets_ok(listing: Listing, lead: LeadProfile) -> bool:
    if not lead.pets:
        return True
    # Клиент с животными: явное «да» или пустое поле; отсекаем только явное «нет».
    return listing.pets_allowed is not False


def availability_ok(listing: Listing, lead: LeadProfile) -> bool:
    if listing.availability == Availability.BUSY:
        # Занят, но известно, что освободится до заезда клиента.
        return bool(
            listing.busy_until and lead.check_in and listing.busy_until < lead.check_in
        )
    return True  # свободно / уточняется — предлагаем, доступность проверит Agent 8


def district_ok(listing: Listing, lead: LeadProfile) -> bool:
    if getattr(lead, "any_district", False):
        return True
    if not lead.districts:
        return True
    listing_canon = canonicalize_district(listing.district) or normalize_key(
        listing.district
    )
    if not listing_canon:
        return False
    for want in lead.districts:
        if not (want or "").strip():
            continue
        want_canon = canonicalize_district(want) or normalize_key(want)
        if want_canon == listing_canon:
            return True
        # Fallback: substring on already-canonical / normalized keys.
        a, b = normalize_key(want_canon), normalize_key(listing_canon)
        if a and b and (a in b or b in a):
            return True
    return False


def capacity_ok(listing: Listing, lead: LeadProfile) -> bool:
    if lead.guests is None or listing.rooms is None:
        return True
    # Прокси: комната вмещает ~2 гостей.
    return listing.rooms * 2 >= lead.guests


def bedrooms_ok(listing: Listing, lead: LeadProfile) -> bool:
    """Клиент назвал число спален — объект должен иметь не меньше."""
    if lead.bedrooms is None or listing.rooms is None:
        return True
    return listing.rooms >= lead.bedrooms


def similarity_score(candidate: Listing, chosen: Listing | None) -> int:
    """Чем выше, тем ближе к объекту, который выбрал клиент."""
    if chosen is None:
        return 0
    score = 0
    if candidate.housing_type and candidate.housing_type == chosen.housing_type:
        score += 2
    if (candidate.rooms is not None and chosen.rooms is not None
            and abs(candidate.rooms - chosen.rooms) <= 1):
        score += 1
    if candidate.district and candidate.district == chosen.district:
        score += 1
    return score


def preference_score(
    candidate: Listing,
    lead: LeadProfile,
    positive: list[dict] | tuple = (),
    negative: list[dict] | tuple = (),
) -> float:
    """Soft ranking from client reactions (Wave 3, policy §9).

    Never filters — a good object is not hidden because of a soft negative
    signal, it only ranks lower.
    """
    score = 0.0
    for signal in negative or ():
        kind = signal.get("kind")
        weight = float(signal.get("weight") or 1.0)
        if kind == "price" and candidate.price_month and lead.budget:
            # Client said "too expensive": penalise anything at/above the cap.
            if candidate.price_month >= lead.budget:
                score -= weight
        elif kind == "location" and lead.districts:
            if not district_ok(candidate, lead):
                score -= weight
        elif kind == "district" and signal.get("value"):
            probe = LeadProfile(districts=[str(signal["value"])])
            if district_ok(candidate, probe):
                score -= weight

    for signal in positive or ():
        kind = signal.get("kind")
        value = str(signal.get("value") or "")
        weight = float(signal.get("weight") or 1.0)
        if kind == "district" and value:
            probe = LeadProfile(districts=[value])
            if district_ok(candidate, probe):
                score += weight
        elif kind == "size" and candidate.rooms is not None and lead.bedrooms:
            if value == "bigger" and candidate.rooms > lead.bedrooms:
                score += weight
            elif value == "smaller" and candidate.rooms < lead.bedrooms:
                score += weight
        elif kind == "price" and candidate.price_month and lead.budget:
            if candidate.price_month < lead.budget:
                score += weight
    return score


def rejected_trait_penalty(
    candidate: Listing,
    rejected_listings: list[Listing] | tuple = (),
) -> float:
    """Rank down objects that look like ones the client explicitly rejected."""
    penalty = 0.0
    for rejected in rejected_listings or ():
        if rejected.object_id == candidate.object_id:
            continue
        if rejected.district and rejected.district == candidate.district:
            penalty += 0.5
        if (rejected.housing_type and rejected.housing_type == candidate.housing_type
                and rejected.rooms is not None and candidate.rooms == rejected.rooms):
            penalty += 0.5
    return penalty


def find_alternatives(
    listings: list[Listing],
    lead: LeadProfile,
    chosen: Listing | None = None,
    limit: int = 3,
    *,
    exclude_ids: list[str] | tuple[str, ...] = (),
    positive_preferences: list[dict] | tuple = (),
    negative_preferences: list[dict] | tuple = (),
    allow_rejected_fallback: bool = False,
) -> list[Listing]:
    """Топ-N альтернатив. Пустой список => спросить у клиента допуск по бюджету/районам.

    Порядок (policy §9): hard filters → availability → price → soft preference
    scoring → reaction-based ranking. ``exclude_ids`` — объекты, которые клиент
    явно отверг: по умолчанию они не попадают в следующую подборку (§10).
    """
    exclude = {chosen.object_id} if chosen else set()
    if lead.preferred_object_id:
        exclude.add(lead.preferred_object_id)
    rejected = {oid for oid in (exclude_ids or ()) if oid}

    def _passes_hard(l: Listing) -> bool:
        return (
            price_sane(l)
            and availability_ok(l, lead)
            and budget_ok(l.price_month, lead)
            and district_ok(l, lead)
            and capacity_ok(l, lead)
            and bedrooms_ok(l, lead)
            and pets_ok(l, lead)
        )

    eligible = [l for l in listings if l.object_id not in exclude and _passes_hard(l)]
    candidates = [l for l in eligible if l.object_id not in rejected]
    if not candidates and allow_rejected_fallback:
        candidates = eligible

    rejected_listings = [l for l in listings if l.object_id in rejected]

    def _rank(l: Listing) -> tuple:
        soft = preference_score(l, lead, positive_preferences, negative_preferences)
        soft -= rejected_trait_penalty(l, rejected_listings)
        return (-similarity_score(l, chosen), -soft, l.price_month or float("inf"))

    candidates.sort(key=_rank)
    return candidates[:limit]


def find_alternatives_for_session(
    listings: list[Listing],
    session,
    limit: int = 3,
    *,
    allow_rejected_fallback: bool = False,
) -> list[Listing]:
    """Session-aware shortlist: applies reaction memory and preference signals."""
    from .reactions import negative_preferences, positive_preferences, rejected_ids

    shown = [oid for oid in (getattr(session, "shown_object_ids", None) or []) if oid]
    exclude = list(rejected_ids(session))
    for oid in shown:
        if oid not in exclude:
            exclude.append(oid)

    return find_alternatives(
        listings,
        session.lead,
        chosen=session.chosen,
        limit=limit,
        exclude_ids=exclude,
        positive_preferences=positive_preferences(session),
        negative_preferences=negative_preferences(session),
        allow_rejected_fallback=allow_rejected_fallback,
    )


def empty_shortlist_reason(listings: list[Listing], lead: LeadProfile) -> str:
    """Which hard stage wiped the pool: district / dates / budget / other."""
    after_price = [l for l in listings if price_sane(l)]
    after_district = [l for l in after_price if district_ok(l, lead)]
    after_budget = [l for l in after_district if budget_ok(l.price_month, lead)]
    after_avail = [l for l in after_budget if availability_ok(l, lead)]
    if after_price and not after_district:
        return "district"
    if after_district and not after_budget and lead.budget is not None:
        return "budget"
    if after_budget and not after_avail:
        return "dates"
    if lead.budget is not None and after_district and not after_budget:
        return "budget"
    return "criteria"
