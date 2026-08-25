from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class WorkerStatus(str, Enum):
    ACTIVE = "ACTIVE"
    COOLDOWN = "COOLDOWN"
    QUARANTINED = "QUARANTINED"
    DISABLED = "DISABLED"


class CheckResult(str, Enum):
    SUCCESS = "SUCCESS"
    CAPTCHA = "CAPTCHA"
    CHALLENGE = "CHALLENGE"
    PROXY_ERROR = "PROXY_ERROR"
    BROWSER_ERROR = "BROWSER_ERROR"
    TIMEOUT = "TIMEOUT"
    PARSE_ERROR = "PARSE_ERROR"
    GENERIC_HOMEPAGE = "GENERIC_HOMEPAGE"
    LISTING_UNAVAILABLE = "LISTING_UNAVAILABLE"
    NOT_FOUND = "NOT_FOUND"


class PricingResult(str, Enum):
    SUCCESS = "SUCCESS"
    NO_PRICE = "NO_PRICE"
    CAPTCHA = "CAPTCHA"
    CHALLENGE = "CHALLENGE"
    PROXY_ERROR = "PROXY_ERROR"
    BROWSER_ERROR = "BROWSER_ERROR"
    TIMEOUT = "TIMEOUT"
    PARSE_ERROR = "PARSE_ERROR"
    INVALID_LISTING = "INVALID_LISTING"


class AssignmentReason(str, Enum):
    INITIAL = "INITIAL"
    INITIAL_MIGRATION = "INITIAL_MIGRATION"
    NEW_OBJECT = "NEW_OBJECT"
    WORKER_QUARANTINED = "WORKER_QUARANTINED"
    WORKER_DISABLED = "WORKER_DISABLED"
    PROXY_FAILED = "PROXY_FAILED"
    MANUAL_REBALANCE = "MANUAL_REBALANCE"
    LOAD_REBALANCE = "LOAD_REBALANCE"


@dataclass
class ProxyConfig:
    id: str
    server: str
    username: str
    password: str


@dataclass
class AirbnbWorker:
    worker_id: str
    proxy_id: str
    proxy_endpoint: str
    user_agent: str
    profile_path: str
    status: WorkerStatus = WorkerStatus.ACTIVE
    assigned_count: int = 0
    active_jobs: int = 0
    last_used_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    captcha_count: int = 0
    cooldown_until: datetime | None = None
    last_pricing_success_at: datetime | None = None
    last_pricing_failure_at: datetime | None = None
    last_pricing_error: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class WorkerAssignment:
    object_id: str
    worker_id: str
    assigned_at: datetime
    assignment_reason: AssignmentReason = AssignmentReason.INITIAL
    updated_at: datetime | None = None


@dataclass
class CalendarCheckResult:
    status: CheckResult
    days: dict[date, bool] = field(default_factory=dict)
    worker_id: str = ""
    proxy_id: str = ""
    object_id: str = ""
    message: str = ""
    diagnostic: dict[str, str] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == CheckResult.SUCCESS

    @property
    def is_captcha(self) -> bool:
        return self.status in {CheckResult.CAPTCHA, CheckResult.CHALLENGE}

    @property
    def should_not_update_availability(self) -> bool:
        return self.status != CheckResult.SUCCESS


@dataclass
class PricingCheckResult:
    status: PricingResult
    worker_id: str = ""
    proxy_id: str = ""
    object_id: str = ""
    message: str = ""
    price_entries: dict[str, dict] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == PricingResult.SUCCESS

    @property
    def should_not_update_prices(self) -> bool:
        """Technical failures must not wipe existing monthly price rows."""
        return self.status in {
            PricingResult.CAPTCHA,
            PricingResult.CHALLENGE,
            PricingResult.PROXY_ERROR,
            PricingResult.BROWSER_ERROR,
            PricingResult.TIMEOUT,
            PricingResult.PARSE_ERROR,
            PricingResult.INVALID_LISTING,
        }

    @property
    def is_captcha(self) -> bool:
        return self.status in {PricingResult.CAPTCHA, PricingResult.CHALLENGE}
