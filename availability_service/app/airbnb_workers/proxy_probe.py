"""Lightweight per-worker proxy connectivity probe (no Airbnb)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..repository import AvailabilityRepository
from .calendar_fetch import profile_directory_lock
from .config import load_worker_pool_config, mask_proxy_server
from .pool import AirbnbWorkerPool
from .registry import AirbnbWorkerRepository

logger = logging.getLogger(__name__)

IPIFY_URL = "https://api.ipify.org?format=json"


@dataclass
class WorkerProxyProbeResult:
    worker_id: str
    proxy_id: str
    passed: bool
    exit_ip: str = ""
    user_agent_ok: bool = False
    profile_exists: bool = False
    message: str = ""


@dataclass
class ProxyProbeReport:
    results: list[WorkerProxyProbeResult] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def unique_exit_ips(self) -> set[str]:
        return {r.exit_ip for r in self.results if r.exit_ip}


def _probe_worker_ip(
    pool: AirbnbWorkerPool,
    worker_id: str,
    *,
    timeout_s: int = 30,
) -> WorkerProxyProbeResult:
    worker = pool.worker_repo.get_worker(worker_id)
    if worker is None:
        return WorkerProxyProbeResult(
            worker_id=worker_id,
            proxy_id="",
            passed=False,
            message="worker not found",
        )
    proxy = pool.get_proxy(worker.proxy_id)
    if proxy is None:
        return WorkerProxyProbeResult(
            worker_id=worker_id,
            proxy_id=worker.proxy_id,
            passed=False,
            message="proxy config missing",
        )

    out = WorkerProxyProbeResult(
        worker_id=worker.worker_id,
        proxy_id=worker.proxy_id,
        passed=False,
    )
    profile_path = Path(worker.profile_path)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        out.message = "Playwright not installed"
        return out

    try:
        with profile_directory_lock(profile_path):
            with sync_playwright() as p:
                args = ["--no-sandbox", "--disable-dev-shm-usage"]
                ctx = p.chromium.launch_persistent_context(
                    str(profile_path),
                    headless=True,
                    args=args,
                    user_agent=worker.user_agent,
                    proxy={
                        "server": proxy.server,
                        "username": proxy.username,
                        "password": proxy.password,
                    },
                )
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(IPIFY_URL, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                body = page.content()
                ua = page.evaluate("() => navigator.userAgent")
                ctx.close()

        out.profile_exists = profile_path.exists() and any(profile_path.iterdir())
        out.user_agent_ok = worker.user_agent in ua
        import json
        import re

        match = re.search(r"\{[^}]+\}", body)
        if match:
            data = json.loads(match.group())
            out.exit_ip = str(data.get("ip", ""))
        out.passed = bool(out.exit_ip) and out.user_agent_ok
        if not out.passed:
            out.message = "missing exit IP or UA mismatch"
        logger.info(
            "PROXY_PROBE worker_id=%s proxy_id=%s endpoint=%s exit_ip=%s pass=%s",
            worker.worker_id,
            worker.proxy_id,
            mask_proxy_server(proxy.server),
            out.exit_ip or "(none)",
            out.passed,
        )
    except Exception as exc:
        msg = str(exc)
        out.message = msg[:200]
        if "proxy" in msg.lower() or "407" in msg or "tunnel" in msg.lower():
            pool.record_check_failure(worker.worker_id, result_kind="PROXY_ERROR")
        logger.warning(
            "PROXY_PROBE_FAIL worker_id=%s proxy_id=%s error=%s",
            worker.worker_id,
            worker.proxy_id,
            out.message,
        )
    return out


def run_proxy_probe(
    repo: AvailabilityRepository,
    *,
    worker_ids: list[str] | None = None,
) -> ProxyProbeReport:
    pool_config = load_worker_pool_config()
    worker_repo = AirbnbWorkerRepository(repo)
    pool = AirbnbWorkerPool(worker_repo, pool_config)
    pool.bootstrap()
    report = ProxyProbeReport()
    targets = worker_ids or [w.worker_id for w in worker_repo.list_workers()]
    for wid in sorted(targets):
        report.results.append(_probe_worker_ip(pool, wid))
    return report


def print_proxy_probe_report(report: ProxyProbeReport) -> None:
    print("\n== AIRBNB WORKER PROXY CONNECTIVITY ==")
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  {r.worker_id} [{status}] proxy_id={r.proxy_id} exit_ip={r.exit_ip or '-'}")
        if r.message:
            print(f"    message: {r.message}")
        print(f"    ua_ok: {r.user_agent_ok} profile_exists: {r.profile_exists}")
    print(f"\n  passed: {report.passed_count}/{len(report.results)}")
    print(f"  unique exit IPs: {len(report.unique_exit_ips)}")
    for ip in sorted(report.unique_exit_ips):
        workers = [r.worker_id for r in report.results if r.exit_ip == ip]
        print(f"    {ip} ← {', '.join(workers)}")
