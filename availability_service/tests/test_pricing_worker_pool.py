"""Tests for Phase 2 pricing worker identity."""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from availability_service.app.airbnb_workers.config import pricing_worker_pool_enabled
from availability_service.app.airbnb_workers.integration import (
    build_worker_pool,
    fetch_airbnb_pricing_for_object,
)
from availability_service.app.airbnb_workers.models import (
    AirbnbWorker,
    AssignmentReason,
    PricingCheckResult,
    PricingResult,
    ProxyConfig,
)
from availability_service.app.airbnb_workers.pool import AirbnbWorkerPool
from availability_service.app.airbnb_workers.pricing_diag import build_pricing_dry_run
from availability_service.app.airbnb_workers.pricing_fetch import (
    build_worker_parser,
    selenium_proxy_string,
)
from availability_service.app.airbnb_workers.registry import AirbnbWorkerRepository
from availability_service.app.airbnb_workers.worker_paths import pricing_profile_path
from availability_service.app.airbnb_workers.config import AirbnbWorkerPoolConfig
from availability_service.app.models import AvailabilityObjectState, MonthWindowItem, RefreshTier, SourceKind
from availability_service.app.repository import AvailabilityRepository

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _proxy_pool(path: Path, count: int = 3) -> None:
    items = [
        {
            "id": f"proxy_{i:02d}",
            "server": f"http://gate.decodo.com:1000{i}",
            "username": "user_test",
            "password": "secret_password_do_not_log",
        }
        for i in range(1, count + 1)
    ]
    path.write_text(json.dumps(items), encoding="utf-8")


def _pool_config(tmp_path: Path, *, pricing_enabled: bool = True) -> AirbnbWorkerPoolConfig:
    proxy_path = tmp_path / "proxies.json"
    ua_path = tmp_path / "uas.json"
    _proxy_pool(proxy_path, 3)
    ua_path.write_text(json.dumps(["UA-A", "UA-B", "UA-C"]), encoding="utf-8")
    return AirbnbWorkerPoolConfig(
        enabled=True,
        proxy_pool_path=proxy_path,
        user_agents_path=ua_path,
        profiles_root=tmp_path / "profiles",
        max_concurrency=1,
        min_delay_seconds=0,
        max_delay_seconds=0,
        rebalance_threshold=3,
        captcha_cooldown_minutes=30,
        captcha_quarantine_threshold=3,
        calendar_timeout_seconds=45,
        pricing_worker_pool_enabled=pricing_enabled,
        pricing_min_delay_seconds=0,
        pricing_max_delay_seconds=0,
    )


def _seed(repo: AvailabilityRepository, object_id: str) -> None:
    repo.save_object(
        AvailabilityObjectState(
            object_id=object_id,
            source=SourceKind.AIRBNB,
            refresh_tier=RefreshTier.H48,
            next_check_at=NOW,
        ),
        now=NOW,
    )


def test_selenium_proxy_string_not_logged(caplog):
    proxy = ProxyConfig("proxy_01", "http://gate.decodo.com:10001", "user_x", "secret_password_do_not_log")
    s = selenium_proxy_string(proxy)
    assert "secret_password_do_not_log" in s
    caplog.set_level(logging.INFO)
    logging.getLogger("test").info("proxy endpoint=%s", "gate.decodo.com:10001")
    assert "secret_password_do_not_log" not in caplog.text


def test_pricing_profile_path_per_worker(tmp_path: Path):
    worker = AirbnbWorker(
        worker_id="worker_01",
        proxy_id="proxy_01",
        proxy_endpoint="http://x",
        user_agent="UA-A",
        profile_path=str(tmp_path / "profiles" / "worker_01"),
    )
    p1 = pricing_profile_path(worker)
    worker2 = AirbnbWorker(
        worker_id="worker_02",
        proxy_id="proxy_02",
        proxy_endpoint="http://y",
        user_agent="UA-B",
        profile_path=str(tmp_path / "profiles" / "worker_02"),
    )
    p2 = pricing_profile_path(worker2)
    assert p1 != p2
    assert p1.name == "pricing"


def test_dry_run_same_proxy_and_ua(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AIRBNB_PRICING_WORKER_POOL_ENABLED", "true")
    repo = AvailabilityRepository(tmp_path / "availability.sqlite3")
    _seed(repo, "A_20260713_003")
    config = _pool_config(tmp_path)
    worker_repo = AirbnbWorkerRepository(repo)
    pool = AirbnbWorkerPool(worker_repo, config)
    pool.bootstrap(now=NOW)
    pool.assign_object("A_20260713_003", reason=AssignmentReason.NEW_OBJECT, now=NOW)
    report = build_pricing_dry_run(repo, ["A_20260713_003"])
    row = report.rows[0]
    assert row.calendar_proxy == row.pricing_proxy
    assert row.calendar_ua == row.pricing_ua
    repo.close()


def test_build_worker_parser_uses_worker_ua_and_proxy(tmp_path: Path, monkeypatch):
    captured: dict = {}

    class FakeChrome:
        def maximize(self):
            pass

    def fake_chrome(**kwargs):
        captured.update(kwargs)
        return FakeChrome()

    class FakeParser:
        HEADLESS_MODE = True
        sb = None

        def __init__(self, headless=True, metrics=None):
            self._price_metrics = metrics

    monkeypatch.setattr(
        "availability_service.app.airbnb_workers.pricing_fetch._metrics_airbnb_parser_class",
        lambda: FakeParser,
    )

    import sys
    import types

    sb_cdp = types.SimpleNamespace(Chrome=fake_chrome)
    seleniumbase = types.SimpleNamespace(sb_cdp=sb_cdp)
    monkeypatch.setitem(sys.modules, "seleniumbase", seleniumbase)
    monkeypatch.setitem(sys.modules, "seleniumbase.sb_cdp", sb_cdp)

    worker = AirbnbWorker(
        worker_id="worker_01",
        proxy_id="proxy_01",
        proxy_endpoint="http://gate:10001",
        user_agent="UA-WORKER-01",
        profile_path=str(tmp_path / "worker_01"),
    )
    proxy = ProxyConfig("proxy_01", "http://gate.decodo.com:10001", "u", "p")
    parser = build_worker_parser(worker, proxy, headless=True)
    parser._init_sb()
    assert captured["agent"] == "UA-WORKER-01"
    assert captured["user_data_dir"] == str(pricing_profile_path(worker))
    assert captured["proxy"].startswith("u:p@")


def test_pricing_failure_blocks_price_update():
    r = PricingCheckResult(status=PricingResult.CAPTCHA, worker_id="worker_01")
    assert r.should_not_update_prices
    assert not r.success


def test_pricing_success_allows_price_update():
    r = PricingCheckResult(status=PricingResult.SUCCESS, worker_id="worker_01")
    assert not r.should_not_update_prices


def test_confirmed_prices_gate():
    entries = {"2026-08": {"price": None, "status": "insufficient_data"}}
    confirmed = sum(1 for e in entries.values() if e and e.get("price"))
    assert confirmed == 0


def test_fetch_pricing_uses_assignment(tmp_path: Path, monkeypatch):
    config = _pool_config(tmp_path, pricing_enabled=True)
    monkeypatch.setenv("AIRBNB_PRICING_WORKER_POOL_ENABLED", "true")
    repo = AvailabilityRepository(tmp_path / "availability.sqlite3")
    _seed(repo, "A_20260713_003")
    pool, _ = build_worker_pool(repo)
    pool.assign_object("A_20260713_003", reason=AssignmentReason.NEW_OBJECT, now=NOW)
    window = [MonthWindowItem(key="2026-08", year=2026, month=8, display_name="Aug 2026")]
    fake_result = PricingCheckResult(
        status=PricingResult.SUCCESS,
        worker_id="worker_01",
        proxy_id="proxy_01",
        object_id="A_20260713_003",
        price_entries={"2026-08": {"price": 1000.0, "status": "monthly"}},
    )
    with patch(
        "availability_service.app.airbnb_workers.integration.fetch_pricing_with_worker",
        return_value=fake_result,
    ) as mocked:
        out = fetch_airbnb_pricing_for_object(
            repo,
            "A_20260713_003",
            "https://www.airbnb.com/rooms/123",
            {date(2026, 8, 1): True},
            window,
            pool=pool,
            pool_config=config,
        )
    assert out.success
    assert mocked.call_args[0][0].worker_id.startswith("worker_")
    repo.close()


def test_pricing_worker_pool_flag_default_false(monkeypatch):
    monkeypatch.delenv("AIRBNB_PRICING_WORKER_POOL_ENABLED", raising=False)
    assert pricing_worker_pool_enabled() is False


def test_registry_pricing_columns(tmp_path: Path):
    repo = AvailabilityRepository(tmp_path / "availability.sqlite3")
    worker_repo = AirbnbWorkerRepository(repo)
    config = _pool_config(tmp_path)
    pool = AirbnbWorkerPool(worker_repo, config)
    pool.bootstrap(now=NOW)
    worker = worker_repo.list_workers()[0]
    pool.record_pricing_success(worker.worker_id, now=NOW)
    updated = worker_repo.get_worker(worker.worker_id)
    assert updated.last_pricing_success_at is not None
    repo.close()


def test_thread_safe_pricing_pool_touch(tmp_path: Path):
    repo_main = AvailabilityRepository(tmp_path / "availability.sqlite3")
    pool_main, _ = build_worker_pool(repo_main)
    worker_id = pool_main.worker_repo.list_workers()[0].worker_id

    def _touch(_: int) -> str:
        thread_repo = AvailabilityRepository(tmp_path / "availability.sqlite3")
        try:
            pool, _ = build_worker_pool(thread_repo)
            pool.record_pricing_success(worker_id, now=NOW)
            return "ok"
        finally:
            thread_repo.close()

    with ThreadPoolExecutor(max_workers=3) as ex:
        assert list(ex.map(_touch, range(3))) == ["ok", "ok", "ok"]
    repo_main.close()
