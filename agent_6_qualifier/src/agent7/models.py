"""Модели данных Agent 7 / Agent 8."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

# Допуск по бюджету клиента по умолчанию, % (env BUDGET_TOLERANCE_PCT)
DEFAULT_BUDGET_TOLERANCE_PCT = float(os.getenv("BUDGET_TOLERANCE_PCT", "10"))


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
    # Цены по месяцам от Агента 1/2 (колонка monthly_prices, JSON):
    # {"2026-09": {"price": 107100, "status": "monthly|prorated|insufficient_data", ...}}
    monthly_prices: dict = field(default_factory=dict)

    def get_price_for_month(self, month: date | str | None = None) -> tuple[Optional[float], str]:
        """Цена для месяца заезда клиента: (price, status).

        status: "monthly" — точная месячная цена Airbnb;
                "prorated" — экстраполяция с доступного отрезка (ориентировочная);
                "insufficient_data" — по этому месяцу данных мало;
                "base" — данных по месяцу нет, взята «Цена за месяц» из таблицы;
                "none" — цены нет вообще.
        """
        key: Optional[str] = None
        if isinstance(month, date):
            key = f"{month.year:04d}-{month.month:02d}"
        elif isinstance(month, str) and month:
            key = month[:7]

        if key and self.monthly_prices:
            entry = self.monthly_prices.get(key)
            if entry:
                price = entry.get("price")
                status = entry.get("status") or "monthly"
                if price:
                    return float(price), status
                if self.price_month:
                    return self.price_month, "insufficient_data"
                return None, "insufficient_data"

        if self.price_month:
            return self.price_month, "base"
        return None, "none"

    def price_quote(self, check_in: date | str | None = None) -> str:
        """Формулировка цены для клиента. Prorated — как ориентировочная."""
        price, status = self.get_price_for_month(check_in)
        if price is None:
            return ""
        text = f"{price:,.0f} THB/мес".replace(",", " ")
        if status in ("prorated", "insufficient_data"):
            return f"ориентировочно {text}, точную цену на ваши даты уточню у владельца"
        return text

    def price_quote_for_period(
        self, check_in: Optional[date], check_out: Optional[date]
    ) -> str:
        """Цена на конкретный период клиента.

        Запрос короче месяца (< 28 ночей) — пропорция от месячной цены:
        месячная / 30 × ночей, округление до сотен THB. Иначе — месячная.
        """
        if not (check_in and check_out):
            return self.price_quote(check_in)
        nights = (check_out - check_in).days
        if nights <= 0 or nights >= 28:
            return self.price_quote(check_in)
        price, _status = self.get_price_for_month(check_in)
        if price is None:
            return ""
        per_period = round(price / 30 * nights / 100) * 100
        period_text = f"{per_period:,.0f} THB".replace(",", " ")
        month_text = f"{price:,.0f} THB/мес".replace(",", " ")
        word = ("ночь" if nights % 10 == 1 and nights % 100 != 11
                else "ночи" if nights % 10 in (2, 3, 4) and nights % 100 not in (12, 13, 14)
                else "ночей")
        return (f"{period_text} за {nights} {word} "
                f"(из расчёта {month_text}), точную цену на ваш период "
                f"уточню у владельца")

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
    budget_tolerance_pct: float = DEFAULT_BUDGET_TOLERANCE_PCT  # ±%, можно уточнить у клиента
    districts: list[str] = field(default_factory=list)
    bedrooms: Optional[int] = None    # сколько спален нужно клиенту
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
