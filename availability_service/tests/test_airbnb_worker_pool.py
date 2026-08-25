"""Tests for Airbnb calendar worker pool (Phase 1)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from availability_service.app.airbnb_workers.calendar_fetch import profile_directory_lock
from availability_service.app.airbnb_workers.page_state import detect_page_state
from availability_service.app.airbnb_workers.config import (
    AirbnbWorkerPoolConfig,
    load_proxy_pool,
    mask_proxy_server,
)
from availability_service.app.airbnb_workers.models import (
    AssignmentReason,
    CheckResult,
    ProxyConfig,
    WorkerStatus,
)
from availability_service.app.airbnb_workers.pool import AirbnbWorkerPool, worker_id_for_proxy
from availability_service.app.airbnb_workers.registry import AirbnbWorkerRepository
from availability_service.app.airbnb_workers.worker_scheduler import pick_airbnb_worker_jobs
from availability_service.app.models import AvailabilityObjectState, RefreshTier, SourceKind
from availability_service.app.repository import AvailabilityRepository

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _proxy_pool(path: Path, count: int) -> None:
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


def _ua_pool(path: Path, count: int) -> None:
    uas = [f"Mozilla/5.0 Chrome/131.0.{i}.0 Safari/537.36" for i in range(count)]
    path.write_text(json.dumps(uas), encoding="utf-8")


def _pool_config(tmp_path: Path, proxies: int = 5, uas: int = 10) -> AirbnbWorkerPoolConfig:
    proxy_path = tmp_path / "proxies.json"
    ua_path = tmp_path / "uas.json"
    _proxy_pool(proxy_path, proxies)
    _ua_pool(ua_path, uas)
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
    )


def _seed_airbnb(repo: AvailabilityRepository, object_ids: list[str]) -> None:
    for oid in object_ids:
        repo.save_object(
            AvailabilityObjectState(
                object_id=oid,
                source=SourceKind.AIRBNB,
                refresh_tier=RefreshTier.H48,
                next_check_at=NOW,
            ),
            now=NOW,
        )


def _make_pool(tmp_path: Path, proxies: int = 5) -> tuple[AirbnbWorkerPool, AvailabilityRepository]:
    repo = AvailabilityRepository(tmp_path / "availability.sqlite3")
    config = _pool_config(tmp_path, proxies=proxies)
    worker_repo = AirbnbWorkerRepository(repo)
    pool = AirbnbWorkerPool(worker_repo, config)
    pool.bootstrap(now=NOW)
    return pool, repo


def _loads(assignments: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for wid in assignments.values():
        out[wid] = out.get(wid, 0) + 1
    return out


def test_worker_id_deterministic():
    assert worker_id_for_proxy("proxy_01") == "worker_01"
    assert worker_id_for_proxy("proxy_10") == "worker_10"


def test_bootstrap_creates_workers_with_unique_profiles(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=5)
    workers = pool.worker_repo.list_workers()
    assert len(workers) == 5
    paths = {w.profile_path for w in workers}
    assert len(paths) == 5
    uas = {w.user_agent for w in workers}
    assert len(uas) == 5
    repo.close()


def test_bootstrap_preserves_identity_on_restart(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=5)
    before = {w.worker_id: (w.user_agent, w.proxy_id) for w in pool.worker_repo.list_workers()}
    pool.bootstrap(now=NOW)
    after = {w.worker_id: (w.user_agent, w.proxy_id) for w in pool.worker_repo.list_workers()}
    assert before == after
    repo.close()


def test_20_objects_5_workers_balanced(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=5)
    ids = [f"A_20260825_{i:03d}" for i in range(1, 21)]
    _seed_airbnb(repo, ids)
    assignments = pool.migrate_balanced(dry_run=True)
    loads = _loads(assignments)
    assert len(assignments) == 20
    assert min(loads.values()) == 4
    assert max(loads.values()) == 4
    repo.close()


def test_23_objects_5_workers_spread(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=5)
    ids = [f"A_20260825_{i:03d}" for i in range(1, 24)]
    _seed_airbnb(repo, ids)
    assignments = pool.migrate_balanced(dry_run=True)
    loads = _loads(assignments)
    assert min(loads.values()) == 4
    assert max(loads.values()) == 5
    assert max(loads.values()) - min(loads.values()) <= 1
    repo.close()


def test_52_objects_10_workers_balanced(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=10)
    ids = [f"A_20260825_{i:03d}" for i in range(1, 53)]
    _seed_airbnb(repo, ids)
    assignments = pool.migrate_balanced(dry_run=True)
    loads = _loads(assignments)
    assert len(assignments) == 52
    assert min(loads.values()) == 5
    assert max(loads.values()) == 6
    assert max(loads.values()) - min(loads.values()) <= 1
    repo.close()


def test_new_objects_least_loaded(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=5)
    ids = [f"A_20260825_{i:03d}" for i in range(1, 21)]
    _seed_airbnb(repo, ids)
    pool.migrate_balanced(dry_run=False, now=NOW)
    loads_before = {w.worker_id: w.assigned_count for w in pool.worker_repo.list_workers()}
    min_before = min(loads_before.values())
    extra = "A_20260825_999"
    _seed_airbnb(repo, [extra])
    worker = pool.assign_object(extra, reason=AssignmentReason.NEW_OBJECT, now=NOW)
    loads_after = {w.worker_id: w.assigned_count for w in pool.worker_repo.list_workers()}
    assert loads_before[worker.worker_id] == min_before
    assert loads_after[worker.worker_id] == min_before + 1
    repo.close()


def test_restart_preserves_assignments(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=5)
    ids = [f"A_20260825_{i:03d}" for i in range(1, 11)]
    _seed_airbnb(repo, ids)
    pool.migrate_balanced(dry_run=False, now=NOW)
    saved = {a.object_id: a.worker_id for a in pool.worker_repo.list_assignments()}
    repo.close()

    repo2 = AvailabilityRepository(tmp_path / "availability.sqlite3")
    config = _pool_config(tmp_path, proxies=5)
    pool2 = AirbnbWorkerPool(AirbnbWorkerRepository(repo2), config)
    pool2.bootstrap(now=NOW)
    restored = {a.object_id: a.worker_id for a in pool2.worker_repo.list_assignments()}
    assert saved == restored
    repo2.close()


def test_disabled_worker_reassign(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=5)
    oid = "A_20260825_001"
    _seed_airbnb(repo, [oid])
    pool.migrate_balanced(dry_run=False, now=NOW)
    first = pool.worker_repo.get_assignment(oid).worker_id
    worker = pool.worker_repo.get_worker(first)
    worker.status = WorkerStatus.DISABLED
    pool.worker_repo.upsert_worker(worker, now=NOW)
    new_worker = pool.ensure_assignment(oid, now=NOW)
    assert new_worker.worker_id != first
    repo.close()


def test_cooldown_worker_gets_no_jobs(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=5)
    ids = [f"A_20260825_{i:03d}" for i in range(1, 6)]
    _seed_airbnb(repo, ids)
    pool.migrate_balanced(dry_run=False, now=NOW)
    worker = pool.worker_repo.list_workers()[0]
    worker.status = WorkerStatus.COOLDOWN
    worker.cooldown_until = NOW + timedelta(hours=1)
    pool.worker_repo.upsert_worker(worker, now=NOW)
    due = repo.due_objects(now=NOW)
    jobs = pick_airbnb_worker_jobs(
        due, pool, pool.worker_repo, pool.config, now=NOW
    )
    assert all(j.worker_id != worker.worker_id for j in jobs)
    repo.close()


def test_quarantined_worker_gets_no_jobs(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=5)
    ids = [f"A_20260825_{i:03d}" for i in range(1, 6)]
    _seed_airbnb(repo, ids)
    pool.migrate_balanced(dry_run=False, now=NOW)
    worker = pool.worker_repo.list_workers()[0]
    worker.status = WorkerStatus.QUARANTINED
    pool.worker_repo.upsert_worker(worker, now=NOW)
    due = repo.due_objects(now=NOW)
    jobs = pick_airbnb_worker_jobs(
        due, pool, pool.worker_repo, pool.config, now=NOW
    )
    assert all(j.worker_id != worker.worker_id for j in jobs)
    repo.close()


def test_captcha_detection():
    assert detect_page_state(
        "https://www.airbnb.com/captcha",
        "Verify",
        "Please verify you're a human",
    ) == CheckResult.CAPTCHA


def test_captcha_does_not_imply_parse_success():
    from availability_service.app.airbnb_workers.models import CalendarCheckResult

    result = CalendarCheckResult(status=CheckResult.CAPTCHA)
    assert result.should_not_update_availability is True


def test_proxy_error_does_not_imply_success():
    from availability_service.app.airbnb_workers.models import CalendarCheckResult

    result = CalendarCheckResult(status=CheckResult.PROXY_ERROR)
    assert result.should_not_update_availability is True


def test_new_worker_does_not_reshuffle_all(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=5)
    ids = [f"A_20260825_{i:03d}" for i in range(1, 21)]
    _seed_airbnb(repo, ids)
    pool.migrate_balanced(dry_run=False, now=NOW)
    before = {a.object_id: a.worker_id for a in pool.worker_repo.list_assignments()}

    proxy_path = tmp_path / "proxies.json"
    _proxy_pool(proxy_path, 6)
    config6 = _pool_config(tmp_path, proxies=6)
    pool6 = AirbnbWorkerPool(AirbnbWorkerRepository(repo), config6)
    pool6.bootstrap(now=NOW)
    pool6.assign_new_objects(now=NOW)
    after = {a.object_id: a.worker_id for a in pool6.worker_repo.list_assignments()}
    assert before == {k: after[k] for k in before}
    repo.close()


def test_profile_paths_unique_and_lock(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=2)
    workers = pool.worker_repo.list_workers()
    p1 = Path(workers[0].profile_path)
    p2 = Path(workers[1].profile_path)
    assert p1 != p2
    with profile_directory_lock(p1):
        with profile_directory_lock(p2):
            pass
    repo.close()


def test_logs_do_not_contain_proxy_password(caplog):
    caplog.set_level(logging.INFO)
    server = mask_proxy_server("http://user:secret_password_do_not_log@gate.decodo.com:10001")
    assert "secret_password_do_not_log" not in server
    record_msg = f"endpoint={server}"
    assert "secret_password_do_not_log" not in record_msg


def test_load_proxy_pool(tmp_path: Path):
    path = tmp_path / "p.json"
    _proxy_pool(path, 2)
    proxies = load_proxy_pool(path)
    assert len(proxies) == 2
    assert proxies[0].password == "secret_password_do_not_log"


def test_captcha_puts_worker_in_cooldown(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=3)
    worker = pool.worker_repo.list_workers()[0]
    pool.record_check_failure(worker.worker_id, result_kind="CAPTCHA", now=NOW)
    updated = pool.worker_repo.get_worker(worker.worker_id)
    assert updated.status == WorkerStatus.COOLDOWN
    assert updated.captcha_count == 1
    assert updated.cooldown_until is not None
    repo.close()


def test_captcha_quarantine_after_threshold(tmp_path: Path):
    pool, repo = _make_pool(tmp_path, proxies=3)
    worker = pool.worker_repo.list_workers()[0]
    for _ in range(3):
        pool.record_check_failure(worker.worker_id, result_kind="CAPTCHA", now=NOW)
    updated = pool.worker_repo.get_worker(worker.worker_id)
    assert updated.status == WorkerStatus.QUARANTINED
    repo.close()


def test_worker_pool_sqlite_per_thread_not_shared(tmp_path: Path):
    """Regression: scheduler ThreadPool must use per-thread repo/pool, not main-thread conn."""
    from concurrent.futures import ThreadPoolExecutor

    from availability_service.app.airbnb_workers.integration import build_worker_pool

    repo_main = AvailabilityRepository(tmp_path / "availability.sqlite3")
    pool_main, _ = build_worker_pool(repo_main)
    pool_main.bootstrap(now=NOW)
    worker_id = pool_main.worker_repo.list_workers()[0].worker_id

    def _touch_pool_from_thread(_: int) -> str:
        thread_repo = AvailabilityRepository(tmp_path / "availability.sqlite3")
        try:
            thread_pool, _ = build_worker_pool(thread_repo)
            thread_pool.record_check_start(worker_id, now=NOW)
            thread_pool.record_check_success(worker_id, now=NOW)
            return "ok"
        finally:
            thread_repo.close()

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(_touch_pool_from_thread, range(3)))
    assert results == ["ok", "ok", "ok"]
    repo_main.close()
