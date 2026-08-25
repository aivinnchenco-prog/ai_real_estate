"""Pricing worker pool diagnostics, dry-run, and smoke helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..repository import AvailabilityRepository
from .config import load_worker_pool_config, mask_proxy_server, pricing_worker_pool_enabled
from .worker_urls import normalize_airbnb_listing_url
from .pool import AirbnbWorkerPool
from .pricing_fetch import build_worker_parser, probe_pricing_listing, worker_pricing_session
from .registry import AirbnbWorkerRepository
from .worker_paths import calendar_storage_path, pricing_profile_path


@dataclass
class PricingIdentityRow:
    object_id: str
    worker_id: str
    proxy_id: str
    calendar_proxy: str
    pricing_proxy: str
    calendar_ua: str
    pricing_ua: str
    calendar_profile: str
    pricing_profile: str


@dataclass
class PricingDryRunReport:
    enabled: bool
    rows: list[PricingIdentityRow] = field(default_factory=list)


def build_pricing_dry_run(
    repo: AvailabilityRepository,
    object_ids: list[str],
) -> PricingDryRunReport:
    pool_config = load_worker_pool_config()
    report = PricingDryRunReport(enabled=pricing_worker_pool_enabled())
    pool = AirbnbWorkerPool(AirbnbWorkerRepository(repo), pool_config)
    pool.bootstrap()
    for oid in object_ids:
        worker = pool.ensure_assignment(oid)
        proxy = pool.get_proxy(worker.proxy_id)
        endpoint = mask_proxy_server(proxy.server) if proxy else "?"
        report.rows.append(
            PricingIdentityRow(
                object_id=oid,
                worker_id=worker.worker_id,
                proxy_id=worker.proxy_id,
                calendar_proxy=endpoint,
                pricing_proxy=endpoint,
                calendar_ua=worker.user_agent,
                pricing_ua=worker.user_agent,
                calendar_profile=str(calendar_storage_path(worker).parent),
                pricing_profile=str(pricing_profile_path(worker)),
            )
        )
    return report


def print_pricing_dry_run(report: PricingDryRunReport) -> None:
    print("\n== PRICING WORKER DRY RUN ==")
    print(f"  AIRBNB_PRICING_WORKER_POOL_ENABLED: {report.enabled}")
    for row in report.rows:
        print(f"\n  {row.object_id}")
        print(f"    worker_id: {row.worker_id}")
        print(f"    proxy_id: {row.proxy_id}")
        print(f"    calendar_proxy: {row.calendar_proxy}")
        print(f"    pricing_proxy: {row.pricing_proxy}")
        print(f"    calendar_ua: {row.calendar_ua[:72]}...")
        print(f"    pricing_ua: {row.pricing_ua[:72]}...")
        print(f"    calendar_profile: {row.calendar_profile}")
        print(f"    pricing_profile: {row.pricing_profile}")
        same_proxy = row.calendar_proxy == row.pricing_proxy
        same_ua = row.calendar_ua == row.pricing_ua
        print(f"    identity_match: proxy={same_proxy} ua={same_ua}")


@dataclass
class PricingConnectivityResult:
    worker_id: str
    passed: bool
    exit_ip: str = ""
    ua_ok: bool = False
    profile_exists: bool = False
    message: str = ""


def probe_pricing_worker_connectivity(
    pool: AirbnbWorkerPool,
    worker_id: str,
    *,
    timeout_s: int = 45,
) -> PricingConnectivityResult:
    """Selenium browser HTTPS probe via worker proxy (no Airbnb parsing)."""
    worker = pool.worker_repo.get_worker(worker_id)
    if worker is None:
        return PricingConnectivityResult(worker_id=worker_id, passed=False, message="worker not found")
    proxy = pool.get_proxy(worker.proxy_id)
    if proxy is None:
        return PricingConnectivityResult(
            worker_id=worker_id, passed=False, message="proxy missing"
        )
    profile = pricing_profile_path(worker)
    out = PricingConnectivityResult(worker_id=worker_id, passed=False)
    out.profile_exists = profile.exists()
    try:
        with worker_pricing_session(
            worker, proxy, acquire_slot=True, min_delay=0, max_delay=0
        ) as parser:
            sb = parser._ensure_sb()
            sb.open("https://api.ipify.org?format=json")
            import time

            time.sleep(2)
            body = sb.get_page_source()
            import json
            import re

            m = re.search(r"\{[^}]+\}", body)
            ip = ""
            if m:
                data = json.loads(m.group())
                ip = data.get("ip") or data.get("query") or ""
            ua = ""
            try:
                ua = sb.execute_script("return navigator.userAgent") or ""
            except Exception:
                pass
            out.exit_ip = ip
            out.ua_ok = worker.user_agent in ua
            out.passed = bool(ip) and out.ua_ok
            out.message = "ok" if out.passed else "ip or ua mismatch"
    except Exception as exc:
        out.message = str(exc)[:200]
    return out
