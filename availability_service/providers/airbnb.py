from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable

from ..app.config import AvailabilityConfig
from ..app.models import AvailabilityStatus, CalendarDay, MonthAvailability, build_month_window
from ..app.status_mapper import (
    AirbnbMonthPolicy,
    build_month_availabilities,
    normalize_calendar,
)
from .airbnb_adapter import (
    collect_prices_for_window,
    create_selenium_price_fetcher,
    fetch_calendar_days_live,
    resolve_agent1_root,
)

CalendarFetcher = Callable[[str], dict[date, bool]]


class LiveProviderDisabled(RuntimeError):
    """Raised when live Airbnb fetch is blocked by feature flags."""


class AirbnbAvailabilityProvider:
    name = "AIRBNB"
    requests_made = 0
    calendar_requests = 0
    price_requests = 0

    def __init__(
        self,
        config: AvailabilityConfig,
        *,
        policy: AirbnbMonthPolicy = AirbnbMonthPolicy.CONTIGUOUS_STAY_REQUIRED,
        min_contiguous_days: int = 5,
        calendar_fetcher: CalendarFetcher | None = None,
        price_collector: Callable[..., dict[str, dict]] | None = None,
    ) -> None:
        self.config = config
        self.policy = policy
        self.min_contiguous_days = min_contiguous_days
        self._calendar_fetcher = calendar_fetcher
        self._price_collector = price_collector

    @property
    def readiness(self) -> str:
        if self.config.airbnb_live_allowed:
            agent1 = resolve_agent1_root()
            return "READY" if agent1 else "DISABLED (Agent1 modules missing)"
        return "DISABLED"

    def fetch_month_availability(
        self,
        source_url: str,
        start_month: date,
        months: int = 12,
    ) -> list[MonthAvailability]:
        if not self.config.airbnb_live_allowed:
            raise LiveProviderDisabled(
                "Airbnb live requests blocked: "
                f"enabled={self.config.enabled} "
                f"airbnb_enabled={self.config.airbnb_enabled} "
                f"dry_run={self.config.dry_run}"
            )

        window = build_month_window(start_month, months=months)
        calendar_raw = self._fetch_calendar(source_url)
        calendar_days = normalize_calendar(calendar_raw)
        price_entries = self._fetch_prices(source_url, window, calendar_raw)

        return build_month_availabilities(
            window,
            calendar_days,
            price_entries,
            self.policy,
            min_contiguous_days=self.min_contiguous_days,
        )

    def _fetch_calendar(self, source_url: str) -> dict[date, bool]:
        if self._calendar_fetcher is not None:
            data = self._calendar_fetcher(source_url)
        else:
            data = fetch_calendar_days_live(source_url)
        self.calendar_requests = 1
        self.requests_made += 1
        return data

    def _fetch_prices(
        self,
        source_url: str,
        window: list,
        calendar_raw: dict[date, bool],
    ) -> dict[str, dict]:
        if self._price_collector is not None:
            entries = self._price_collector(source_url, window, calendar_raw)
            self.price_requests = len(window)
            self.requests_made += self.price_requests
            return entries

        fetch_price, cleanup, get_metrics = create_selenium_price_fetcher(source_url)
        try:
            entries = collect_prices_for_window(
                fetch_price,
                calendar_raw,
                window,
                min_segment_days=self.min_contiguous_days,
            )
            self.price_requests = get_metrics().fetch_calls
            self.requests_made += self.price_requests
            return entries
        finally:
            cleanup()


def provider_readiness_label(config: AvailabilityConfig) -> str:
    return AirbnbAvailabilityProvider(config).readiness


def save_provider_calendar(
    repository,
    object_id: str,
    calendar_days: list,
    *,
    replace: bool = True,
    fetched_at: datetime | None = None,
) -> int:
    """Persist provider CalendarDay list to SQLite (no Notion writes)."""
    from ..app.models import DailyAvailabilityStatus, SourceKind, calendar_day

    normalized: list[CalendarDay] = []
    for item in calendar_days:
        if isinstance(item, CalendarDay):
            if item.source == SourceKind.UNKNOWN:
                normalized.append(
                    calendar_day(
                        item.date,
                        status=item.status,
                        source=SourceKind.AIRBNB,
                        source_state=item.source_state,
                        fetched_at=item.fetched_at,
                        last_successful_refresh=item.last_successful_refresh,
                    )
                )
            else:
                normalized.append(item)
        else:
            normalized.append(
                calendar_day(
                    item.date,
                    available=item.available,
                    source=SourceKind.AIRBNB,
                    source_state=item.source_state,
                )
            )
    when = fetched_at or datetime.now(timezone.utc)
    if replace:
        return repository.replace_calendar_days(
            object_id,
            normalized,
            fetched_at=when,
            last_successful_refresh=when,
        )
    return repository.upsert_calendar_days(
        object_id,
        normalized,
        fetched_at=when,
        last_successful_refresh=when,
    )


def parser_error_months(
    window: list,
    error: str,
) -> list[MonthAvailability]:
    """All months UNKNOWN when live parser fails (no price override)."""
    return [
        MonthAvailability(
            year=item.year,
            month=item.month,
            status=AvailabilityStatus.UNKNOWN,
            note=error,
        )
        for item in window
    ]
