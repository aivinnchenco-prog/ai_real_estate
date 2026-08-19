from datetime import date

import pytest

from availability_service.app.config import AvailabilityConfig
from availability_service.app.models import RefreshTier, WindowStartMode, build_month_window
from availability_service.providers.airbnb import AirbnbAvailabilityProvider, LiveProviderDisabled


def _config(**kwargs) -> AvailabilityConfig:
    from pathlib import Path

    runtime = Path("/tmp/availability-airbnb-test")
    base = {
        "enabled": False,
        "dry_run": True,
        "notion_api_key": "ntn_test",
        "source_database_id": "source",
        "target_database_id": "target",
        "target_data_source_id": "",
        "runtime_dir": runtime,
        "sqlite_path": runtime / "availability.sqlite3",
        "default_refresh_tier": RefreshTier.H12,
        "max_concurrency": 2,
        "dry_run_batch_size": 25,
        "object_concurrency": 1,
        "calendar_concurrency": 1,
        "price_concurrency": 1,
        "browser_max_instances": 2,
        "batch_safe_max_objects": 5,
        "window_start_mode": WindowStartMode.NEXT_MONTH,
        "airbnb_enabled": False,
    }
    base.update(kwargs)
    return AvailabilityConfig(**base)


def test_provider_readiness_disabled_by_default():
    provider = AirbnbAvailabilityProvider(_config())
    assert provider.readiness == "DISABLED"


def test_provider_readiness_ready_when_flags_allow():
    provider = AirbnbAvailabilityProvider(
        _config(enabled=True, dry_run=False, airbnb_enabled=True)
    )
    assert provider.readiness.startswith("READY")


def test_live_fetch_blocked_when_disabled():
    provider = AirbnbAvailabilityProvider(_config())
    with pytest.raises(LiveProviderDisabled):
        provider.fetch_month_availability("https://airbnb.com/rooms/1", date(2026, 9, 1))


def test_live_fetch_blocked_when_dry_run():
    provider = AirbnbAvailabilityProvider(
        _config(enabled=True, dry_run=True, airbnb_enabled=True)
    )
    with pytest.raises(LiveProviderDisabled):
        provider.fetch_month_availability("https://airbnb.com/rooms/1", date(2026, 9, 1))


def test_provider_with_injected_fetchers_no_network():
    window = build_month_window(date(2026, 9, 1), months=12)
    cal = {date(2026, 9, 1): True, date(2026, 9, 2): True}

    def calendar_fetcher(url: str):
        return cal

    def price_collector(url, win, calendar_raw):
        return {
            item.key: {
                "price": 10000,
                "status": "monthly",
                "period_used": "2026-09-01/2026-09-30",
            }
            for item in win
        }

    provider = AirbnbAvailabilityProvider(
        _config(enabled=True, dry_run=False, airbnb_enabled=True),
        calendar_fetcher=calendar_fetcher,
        price_collector=price_collector,
    )
    rows = provider.fetch_month_availability("https://airbnb.com/rooms/1", date(2026, 9, 1))
    assert len(rows) == 12
    assert provider.calendar_requests == 1
    assert provider.price_requests == 12
    assert provider.requests_made == 13
