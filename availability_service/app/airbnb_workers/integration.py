from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..config import AvailabilityConfig
from ..models import AvailabilityObjectState, SourceKind
from ..repository import AvailabilityRepository
from .calendar_fetch import fetch_calendar_with_worker
from .config import AirbnbWorkerPoolConfig, load_worker_pool_config
from .models import CalendarCheckResult, CheckResult
from .pool import AirbnbWorkerPool
from .registry import AirbnbWorkerRepository

logger = logging.getLogger(__name__)


def worker_pool_enabled(config: AvailabilityConfig | None = None) -> bool:
    pool_config = load_worker_pool_config()
    return pool_config.enabled


def build_worker_pool(repo: AvailabilityRepository) -> tuple[AirbnbWorkerPool, AirbnbWorkerPoolConfig]:
    config = load_worker_pool_config()
    worker_repo = AirbnbWorkerRepository(repo)
    pool = AirbnbWorkerPool(worker_repo, config)
    pool.bootstrap()
    return pool, config


from urllib.parse import urlparse, urlunparse


def _normalize_airbnb_listing_url(url: str) -> str:
    """US proxies fail on .ru and long tracking query strings; calendar needs clean .com room URL."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.replace("airbnb.ru", "airbnb.com")
    if not host.startswith("www.") and "airbnb.com" in host:
        host = "www." + host.lstrip(".")
    if "airbnb.com" not in host:
        host = host.replace("airbnb.ru", "www.airbnb.com")
    scheme = parsed.scheme or "https"
    path = parsed.path or "/"
    return urlunparse((scheme, host, path, "", "", ""))


def fetch_airbnb_calendar_for_object(
    repo: AvailabilityRepository,
    object_id: str,
    listing_url: str,
    *,
    timeout_s: int | None = None,
    pool: AirbnbWorkerPool | None = None,
    pool_config: AirbnbWorkerPoolConfig | None = None,
) -> CalendarCheckResult:
    pool_config = pool_config or load_worker_pool_config()
    if pool is None:
        pool, _ = build_worker_pool(repo)
    worker = pool.ensure_assignment(object_id)
    proxy = pool.get_proxy(worker.proxy_id)
    if proxy is None:
        return CalendarCheckResult(
            status=CheckResult.PROXY_ERROR,
            worker_id=worker.worker_id,
            proxy_id=worker.proxy_id,
            object_id=object_id,
            message=f"proxy config missing for {worker.proxy_id}",
        )
    pool.record_check_start(worker.worker_id)
    result = fetch_calendar_with_worker(
        worker,
        proxy,
        _normalize_airbnb_listing_url(listing_url),
        object_id=object_id,
        timeout_s=timeout_s or pool_config.calendar_timeout_seconds,
        max_concurrency=pool_config.max_concurrency,
        min_delay=pool_config.min_delay_seconds,
        max_delay=pool_config.max_delay_seconds,
    )
    if result.success:
        pool.record_check_success(worker.worker_id)
    else:
        pool.record_check_failure(worker.worker_id, result_kind=result.status.value)
    return result


def calendar_failure_message(result: CalendarCheckResult) -> str:
    return f"calendar_{result.status.value.lower()}: {result.message or result.status.value}"


def should_schedule_calendar_retry(result: CalendarCheckResult) -> bool:
    return result.should_not_update_availability
