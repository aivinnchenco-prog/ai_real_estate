"""Модели данных Agent 7 / Agent 8."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class Availability(str, Enum):
    FREE = "свободно"
    BUSY = "занято"
    UNKNOWN = "уточняется"


class OwnerChannel(str, Enum):
    """Приоритет каналов связи с собственником (по убыванию)."""
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    AIRBNB = "airbnb"
    FB_MARKETPLACE = "fb_marketplace"


@dataclass
class Listing:
    """Объект из Notion CRM (только поля, нужные квалификатору)."""
    object_id: str                     # YYYYMMDD_NNN
    page_id: str = ""
    title: str = ""
    district: str = ""
    housing_type: str = ""
    rooms: Optional[int] = None
    price_month: Optional[float] = None
    pets_allowed: Optional[bool] = None    # None = в таблице не указано
    photos_url: str = ""                   # R2-галерея (колонка «Фото»)
    tg_post_url: str = ""                  # post_url_telegram
    source_url: str = ""                   # «Источник объявления»
    calendar_url: str = ""                 # «Календарь»: где проверять доступность дат
    owner_name: str = ""
    owner_whatsapp: str = ""
    owner_telegram: str = ""
    availability: Availability = Availability.UNKNOWN
    busy_until: Optional[date] = None

    @property
    def source_is_airbnb(self) -> bool:
        return "airbnb." in self.source_url.lower()

    @property
    def availability_calendar(self) -> str:
        """URL для автопроверки дат: колонка «Календарь», иначе Airbnb-источник.

        Пустая строка = календаря нет, доступность узнаём вручную у владельца.
        """
        cal = self.calendar_url.strip()
        if cal and cal.lower() not in ("ручной", "manual", "-"):
            return cal
        if self.source_is_airbnb:
            return self.source_url.strip()
        return ""

    def owner_channel(self) -> Optional[tuple[OwnerChannel, str]]:
        """Первый доступный канал связи с владельцем по приоритету.

        WA → TG → Airbnb DM (ссылка объявления) → FB Marketplace DM.
        """
        if self.owner_whatsapp.strip():
            return OwnerChannel.WHATSAPP, self.owner_whatsapp.strip()
        if self.owner_telegram.strip():
            return OwnerChannel.TELEGRAM, self.owner_telegram.strip()
        src = self.source_url.strip()
        if self.source_is_airbnb:
            return OwnerChannel.AIRBNB, src
        if "facebook." in src.lower() and src:
            return OwnerChannel.FB_MARKETPLACE, src
        return None


@dataclass
class LeadProfile:
    """Квалификация клиента. Заполняется мягко, по ходу диалога."""
    name: str = ""
    full_name: str = ""               # ФИО для брони
    citizenship: str = ""               # гражданство для брони
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    stay_months: Optional[float] = None
    budget: Optional[float] = None
    budget_tolerance_pct: float = 10.0     # по умолчанию ±10%; можно уточнить у клиента
    districts: list[str] = field(default_factory=list)
    guests: Optional[int] = None
    pets: Optional[bool] = None
    preferred_object_id: str = ""
    source_channel: str = ""

    def missing_core_fields(self) -> list[str]:
        """Что ещё нужно для подбора (минимум для матчинга)."""
        missing = []
        if not self.check_in:
            missing.append("даты заезда")
        if not self.guests:
            missing.append("количество человек")
        if self.budget is None:
            missing.append("бюджет")
        if not self.districts:
            missing.append("район")
        return missing
