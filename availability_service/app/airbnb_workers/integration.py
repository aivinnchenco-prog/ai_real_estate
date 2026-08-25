from __future__ import annotations

import logging

from ..config import AvailabilityConfig
from ..models import MonthWindowItem
from ..repository import AvailabilityRepository
from .calendar_fetch import fetch_calendar_with_worker, worker_execution_slot
from .config import AirbnbWorkerPoolConfig, load_worker_pool_config, pricing_worker_pool_enabled
from .models import CalendarCheckResult, CheckResult, PricingCheckResult, PricingResult
from .pool import AirbnbWorkerPool
from .pricing_fetch import fetch_pricing_with_worker
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


from .worker_urls import normalize_airbnb_listing_url, normalize_airbnb_pricing_url


def should_skip_pricing_after_calendar(calendar_result: CalendarCheckResult) -> bool:
    """Do not run pricing on same unhealthy worker after calendar hard failure."""
    if calendar_result.success:
        return False
    return calendar_result.status in {
        CheckResult.CAPTCHA,
        CheckResult.CHALLENGE,
        CheckResult.PROXY_ERROR,
        CheckResult.BROWSER_ERROR,
        CheckResult.TIMEOUT,
    }


def fetch_airbnb_calendar_for_object(
    repo: AvailabilityRepository,
    object_id: str,
    listing_url: str,
    *,
    timeout_s: int | None = None,
    pool: AirbnbWorkerPool | None = None,
    pool_config: AirbnbWorkerPoolConfig | None = None,
    acquire_slot: bool = True,
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
        normalize_airbnb_listing_url(listing_url),
        object_id=object_id,
        timeout_s=timeout_s or pool_config.calendar_timeout_seconds,
        max_concurrency=pool_config.max_concurrency,
        min_delay=pool_config.min_delay_seconds,
        max_delay=pool_config.max_delay_seconds,
        acquire_slot=acquire_slot,
    )
    if result.success:
        pool.record_check_success(worker.worker_id)
    else:
        pool.record_check_failure(worker.worker_id, result_kind=result.status.value)
    return result


def fetch_airbnb_pricing_for_object(
    repo: AvailabilityRepository,
    object_id: str,
    listing_url: str,
    availability: dict[date, bool],
    window: list[MonthWindowItem],
    *,
    pool: AirbnbWorkerPool | None = None,
    pool_config: AirbnbWorkerPoolConfig | None = None,
    price_object_max_seconds: float = 0,
    acquire_slot: bool = True,
    worker_id: str | None = None,
) -> PricingCheckResult:
    pool_config = pool_config or load_worker_pool_config()
    if pool is None:
        pool, _ = build_worker_pool(repo)
    if worker_id:
        worker = pool.worker_repo.get_worker(worker_id)
        if worker is None:
            return PricingCheckResult(
                status=PricingResult.BROWSER_ERROR,
                object_id=object_id,
                message=f"worker not found: {worker_id}",
            )
    else:
        worker = pool.ensure_assignment(object_id)
    proxy = pool.get_proxy(worker.proxy_id)
    if proxy is None:
        return PricingCheckResult(
            status=PricingResult.PROXY_ERROR,
            worker_id=worker.worker_id,
            proxy_id=worker.proxy_id,
            object_id=object_id,
            message=f"proxy config missing for {worker.proxy_id}",
        )
    result = fetch_pricing_with_worker(
        worker,
        proxy,
        normalize_airbnb_pricing_url(listing_url),
        availability,
        window,
        object_id=object_id,
        price_object_max_seconds=price_object_max_seconds,
        max_concurrency=pool_config.max_concurrency,
        min_delay=pool_config.pricing_min_delay_seconds,
        max_delay=pool_config.pricing_max_delay_seconds,
        acquire_slot=acquire_slot,
    )
    if result.success:
        pool.record_pricing_success(worker.worker_id)
    elif result.should_not_update_prices:
        pool.record_pricing_failure(worker.worker_id, result_kind=result.status.value)
    return result


def calendar_failure_message(result: CalendarCheckResult) -> str:
    return f"calendar_{result.status.value.lower()}: {result.message or result.status.value}"


def pricing_failure_message(result: PricingCheckResult) -> str:
    return f"pricing_{result.status.value.lower()}: {result.message or result.status.value}"


def should_schedule_calendar_retry(result: CalendarCheckResult) -> bool:
    return result.should_not_update_availability


def worker_slot_for_object(
    pool: AirbnbWorkerPool,
    object_id: str,
    pool_config: AirbnbWorkerPoolConfig,
):
    """Hold worker/proxy slot across calendar→pricing for one object."""
    worker = pool.ensure_assignment(object_id)
    return worker_execution_slot(
        worker.worker_id,
        max_concurrency=pool_config.max_concurrency,
        min_delay=pool_config.min_delay_seconds,
        max_delay=pool_config.max_delay_seconds,
    ), worker
