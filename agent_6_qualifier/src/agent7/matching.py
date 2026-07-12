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

from .models import Availability, LeadProfile, Listing


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
    if not lead.districts:
        return True
    d = listing.district.strip().lower()
    return any(want.strip().lower() in d or d in want.strip().lower()
               for want in lead.districts if want.strip())


def capacity_ok(listing: Listing, lead: LeadProfile) -> bool:
    if lead.guests is None or listing.rooms is None:
        return True
    # Прокси: комната вмещает ~2 гостей.
    return listing.rooms * 2 >= lead.guests


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


def find_alternatives(
    listings: list[Listing],
    lead: LeadProfile,
    chosen: Listing | None = None,
    limit: int = 3,
) -> list[Listing]:
    """Топ-N альтернатив. Пустой список => спросить у клиента допуск по бюджету/районам."""
    exclude = {chosen.object_id} if chosen else set()
    if lead.preferred_object_id:
        exclude.add(lead.preferred_object_id)

    candidates = [
        l for l in listings
        if l.object_id not in exclude
        and availability_ok(l, lead)
        and budget_ok(l.price_month, lead)
        and district_ok(l, lead)
        and capacity_ok(l, lead)
        and pets_ok(l, lead)
    ]
    candidates.sort(
        key=lambda l: (-similarity_score(l, chosen), l.price_month or float("inf"))
    )
    return candidates[:limit]
