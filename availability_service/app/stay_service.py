"""Stay availability service — reads SQLite daily calendar (source of truth)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .models import AvailabilityFreshness, DailyAvailabilityStatus, StayAvailabilityResult
from .repository import AvailabilityRepository
from .stay_matcher import add_calendar_months, check_stay_availability
from .stay_summary import format_stay_availability_summary


class AvailabilityStayService:
    def __init__(self, repository: AvailabilityRepository) -> None:
        self.repository = repository

    def check_object_stay(
        self,
        object_id: str,
        check_in: date,
        stay_months: int,
    ) -> StayAvailabilityResult:
        check_out = add_calendar_months(check_in, stay_months)
        calendar_days = self.repository.get_calendar_days(object_id, check_in, check_out)
        last_checked = self.repository.get_last_calendar_refresh(object_id)
        freshness = self.repository.get_object_calendar_freshness(object_id)
        return check_stay_availability(
            object_id,
            check_in,
            stay_months,
            calendar_days,
            last_checked_at=last_checked,
            freshness=freshness,
        )

    def get_stay_availability_for_agent(
        self,
        object_id: str,
        check_in: date,
        stay_months: int,
        locale: str = "ru",
    ) -> dict:
        """Internal Agent6-ready payload (not wired to Agent6 in this task)."""
        result = self.check_object_stay(object_id, check_in, stay_months)
        self.repository.validate_freshness_invariant(object_id)
        return {
            "object_id": result.object_id,
            "status": result.status.value,
            "check_in": result.check_in.isoformat(),
            "check_out": result.check_out.isoformat(),
            "stay_months": result.stay_months,
            "freshness": result.freshness.value,
            "last_checked_at": result.last_checked_at.isoformat() if result.last_checked_at else None,
            "blocked_ranges": result.blocked_ranges,
            "unknown_ranges": result.unknown_ranges,
            "available_ranges": result.available_ranges,
            "summary": format_stay_availability_summary(result, locale=locale),
        }

    def get_calendar_view(
        self,
        object_id: str,
        date_from: date,
        date_to: date,
    ) -> dict:
        """Internal website-ready calendar slice (no HTTP API in this task)."""
        days = self.repository.get_calendar_days(object_id, date_from, date_to)
        last_checked = self.repository.get_last_calendar_refresh(object_id)
        freshness = self.repository.get_object_calendar_freshness(object_id)
        return {
            "object_id": object_id,
            "freshness": freshness.value,
            "last_checked_at": last_checked.isoformat() if last_checked else None,
            "days": [
                {
                    "date": day.date.isoformat(),
                    "status": day.status.value,
                }
                for day in days
            ],
        }

    def fill_missing_days_as_unknown(
        self,
        object_id: str,
        date_from: date,
        date_to: date,
    ) -> list[date]:
        """Dates in [date_from, date_to) with no row in DB."""
        stored = self.repository.get_calendar_days(object_id, date_from, date_to)
        known = {day.date for day in stored}
        missing: list[date] = []
        day = date_from
        while day < date_to:
            if day not in known:
                missing.append(day)
            day += timedelta(days=1)
        return missing
