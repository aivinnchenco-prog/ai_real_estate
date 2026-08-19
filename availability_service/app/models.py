from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum


class SourceKind(str, Enum):
    AIRBNB = "AIRBNB"
    FACEBOOK = "FACEBOOK"
    UNKNOWN = "UNKNOWN"


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    REQUIRES_CONFIRMATION = "REQUIRES_CONFIRMATION"
    UNKNOWN = "UNKNOWN"


class DailyAvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class AvailabilityFreshness(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"


class RefreshTier(str, Enum):
    FIRST_REFRESH = "FIRST_REFRESH"
    H1 = "1H"
    H12 = "12H"
    H24 = "24H"
    H48 = "48H"
    H78 = "78H"


class RefreshStatus(str, Enum):
    IDLE = "IDLE"
    QUEUED = "QUEUED"
    CHECKING = "CHECKING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"


class SourceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    SOLD = "SOLD"
    REMOVED = "REMOVED"
    UNKNOWN = "UNKNOWN"


class WindowStartMode(str, Enum):
    CURRENT_MONTH = "current_month"
    NEXT_MONTH = "next_month"
    SEP_2026_AUG_2027 = "sep_2026_aug_2027"


# Fixed availability table window: Sep 2026 → Aug 2027 (12 Notion month columns).
FIXED_AVAILABILITY_WINDOW_START = date(2026, 9, 1)
FIXED_AVAILABILITY_WINDOW_MONTHS = 12


TIER_HOURS: dict[RefreshTier, int] = {
    RefreshTier.FIRST_REFRESH: 0,
    RefreshTier.H1: 1,
    RefreshTier.H12: 12,
    RefreshTier.H24: 24,
    RefreshTier.H48: 48,
    RefreshTier.H78: 78,
}

MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


@dataclass(frozen=True)
class MonthWindowItem:
    year: int
    month: int
    key: str
    display_name: str


@dataclass(frozen=True)
class PropertySource:
    object_id: str
    name: str
    source: SourceKind
    source_url: str
    notion_page_id: str = ""
    calendar_url: str = ""


@dataclass(frozen=True)
class CalendarDay:
    date: date
    status: DailyAvailabilityStatus = DailyAvailabilityStatus.UNKNOWN
    source: SourceKind = SourceKind.UNKNOWN
    source_state: str | None = None
    fetched_at: datetime | None = None
    last_successful_refresh: datetime | None = None

    @property
    def available(self) -> bool:
        return self.status == DailyAvailabilityStatus.AVAILABLE


def calendar_day(
    day: date,
    *,
    status: DailyAvailabilityStatus | None = None,
    available: bool | None = None,
    source: SourceKind = SourceKind.UNKNOWN,
    source_state: str | None = None,
    fetched_at: datetime | None = None,
    last_successful_refresh: datetime | None = None,
) -> CalendarDay:
    """Build CalendarDay; `available` bool is legacy input normalized to status."""
    if status is None:
        if available is True:
            status = DailyAvailabilityStatus.AVAILABLE
        elif available is False:
            status = DailyAvailabilityStatus.BLOCKED
        else:
            status = DailyAvailabilityStatus.UNKNOWN
    return CalendarDay(
        date=day,
        status=status,
        source=source,
        source_state=source_state,
        fetched_at=fetched_at,
        last_successful_refresh=last_successful_refresh,
    )


@dataclass
class StayAvailabilityResult:
    object_id: str
    status: AvailabilityStatus
    check_in: date
    check_out: date
    stay_months: int
    checked_days_count: int = 0
    available_dates: list[date] = field(default_factory=list)
    blocked_dates: list[date] = field(default_factory=list)
    unknown_dates: list[date] = field(default_factory=list)
    available_ranges: list[dict[str, str]] = field(default_factory=list)
    blocked_ranges: list[dict[str, str]] = field(default_factory=list)
    unknown_ranges: list[dict[str, str]] = field(default_factory=list)
    last_checked_at: datetime | None = None
    freshness: AvailabilityFreshness = AvailabilityFreshness.MISSING
    unavailable_dates: list[date] = field(default_factory=list)
    missing_dates: list[date] = field(default_factory=list)
    checked_days: list[date] = field(default_factory=list)


@dataclass(frozen=True)
class MonthAvailability:
    year: int
    month: int
    status: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    price: float | None = None
    currency: str = "THB"
    pricing_status: str | None = None
    period_used: str | None = None
    based_on_days: int | None = None
    note: str | None = None


@dataclass(frozen=True)
class MonthlyRow:
    object_id: str
    month_key: str
    status: str
    price: float | None = None
    currency: str = "THB"
    pricing_status: str | None = None
    period_used: str | None = None
    based_on_days: int | None = None
    fetched_at: datetime | None = None


@dataclass
class AvailabilityObjectState:
    object_id: str
    refresh_tier: RefreshTier = RefreshTier.H48
    last_checked_at: datetime | None = None
    next_check_at: datetime | None = None
    refresh_status: RefreshStatus = RefreshStatus.IDLE
    source_status: SourceStatus = SourceStatus.UNKNOWN
    last_error: str = ""
    retry_count: int = 0
    name: str = ""
    source: SourceKind = SourceKind.UNKNOWN
    source_url: str = ""
    notion_page_id: str = ""
    last_calendar_refresh_at: datetime | None = None
    status_reason: str = ""


@dataclass
class RefreshJob:
    object_id: str
    status: RefreshStatus = RefreshStatus.QUEUED
    job_id: int | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""


@dataclass(frozen=True)
class FieldMatch:
    logical: str
    property_name: str | None
    property_type: str | None
    status: str
    candidates: tuple[str, ...] = ()
    property_id: str | None = None


@dataclass
class SourceFieldMapping:
    object_id: FieldMatch | None = None
    name: FieldMatch | None = None
    source: FieldMatch | None = None
    source_url: FieldMatch | None = None
    calendar_url: FieldMatch | None = None

    def as_list(self) -> list[FieldMatch]:
        return [
            m for m in (
                self.object_id,
                self.name,
                self.source,
                self.source_url,
                self.calendar_url,
            )
            if m
        ]

    def mapped_property_names(self) -> list[str]:
        names: list[str] = []
        for match in self.as_list():
            if match.property_name and match.status == "mapped":
                names.append(match.property_name)
        return names

    def unmapped(self) -> list[FieldMatch]:
        return [m for m in self.as_list() if m.status != "mapped"]


@dataclass
class TargetFieldMapping:
    object_id: FieldMatch | None = None
    object_name: FieldMatch | None = None
    source: FieldMatch | None = None
    calendar_url: FieldMatch | None = None
    last_checked: FieldMatch | None = None
    next_check: FieldMatch | None = None
    refresh_tier: FieldMatch | None = None
    refresh_status: FieldMatch | None = None
    source_status: FieldMatch | None = None
    last_error: FieldMatch | None = None
    month_columns: list[str] = field(default_factory=list)
    technical_columns: list[str] = field(default_factory=list)

    def display_column_order(self) -> list[str]:
        from .target_column_order import locked_columns_present

        names = set(self.month_columns) | set(self.technical_columns)
        for match in self.as_list():
            if match.property_name:
                names.add(match.property_name)
        return locked_columns_present(names)

    def technical_field_names(self) -> list[str]:
        from .target_column_order import LOCKED_TECHNICAL_COLUMNS

        names = set(self.technical_columns)
        for match in self.as_list():
            if match.property_name and match.status == "mapped":
                names.add(match.property_name)
        return [c for c in LOCKED_TECHNICAL_COLUMNS if c in names]

    def as_list(self) -> list[FieldMatch]:
        return [
            m for m in (
                self.object_id,
                self.object_name,
                self.source,
                self.calendar_url,
                self.last_checked,
                self.next_check,
                self.refresh_tier,
                self.refresh_status,
                self.source_status,
                self.last_error,
            )
            if m
        ]

    def unmapped(self) -> list[FieldMatch]:
        return [m for m in self.as_list() if m.status != "mapped"]


@dataclass
class NotionSchema:
    database_id: str
    title: str
    properties: dict[str, dict] = field(default_factory=dict)
    object_kind: str = "database"
    error: str = ""
    data_source_id: str = ""

    def property_summaries(self) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for name, meta in self.properties.items():
            rows.append((name, str(meta.get("type") or "unknown")))
        return rows


@dataclass
class DryRunSummary:
    objects_read: int = 0
    airbnb: int = 0
    facebook: int = 0
    unknown: int = 0
    due: int = 0
    would_enqueue: int = 0
    tier_1h: int = 0
    tier_12h: int = 0
    tier_24h: int = 0
    window: list[MonthWindowItem] = field(default_factory=list)
    effective_window_start: date | None = None
    window_start_mode: str = ""
    column_order_lock: bool = False
    column_order_message: str = ""
    source_mapping: SourceFieldMapping | None = None
    source_schema: NotionSchema | None = None
    target_schema: NotionSchema | None = None
    target_mapping: TargetFieldMapping | None = None
    month_columns: list[str] = field(default_factory=list)
    technical_columns: list[str] = field(default_factory=list)
    sqlite_path: str = ""
    notion_writes: int = 0
    airbnb_requests: int = 0
    facebook_requests: int = 0
    refresh_jobs_created: int = 0
    airbnb_provider_readiness: str = ""
    daily_calendar_storage: str = "READY"
    exact_stay_matcher: str = "READY"
    date_ranges: str = "READY"
    freshness: str = "READY"
    agent6_interface: str = "READY (NOT CONNECTED)"
    website_calendar_interface: str = "READY (NOT CONNECTED)"
    notes: list[str] = field(default_factory=list)


def get_effective_window_start(
    now: date | datetime,
    mode: WindowStartMode | str,
) -> date:
    """First day of the effective availability window start month."""
    if isinstance(now, datetime):
        today = now.date()
    else:
        today = now
    mode_value = mode.value if isinstance(mode, WindowStartMode) else str(mode).strip().lower()
    if mode_value == WindowStartMode.SEP_2026_AUG_2027.value:
        return FIXED_AVAILABILITY_WINDOW_START
    if mode_value == WindowStartMode.CURRENT_MONTH.value:
        return date(today.year, today.month, 1)
    if mode_value == WindowStartMode.NEXT_MONTH.value:
        if today.month == 12:
            return date(today.year + 1, 1, 1)
        return date(today.year, today.month + 1, 1)
    raise ValueError(f"Unsupported window start mode: {mode!r}")


def get_availability_window(
    mode: WindowStartMode | str,
    *,
    now: date | datetime | None = None,
    months: int = FIXED_AVAILABILITY_WINDOW_MONTHS,
) -> list[MonthWindowItem]:
    """Build the service availability month window (Notion table columns)."""
    start = get_effective_window_start(now or date.today(), mode)
    return build_month_window(start, months=months)


def window_calendar_bounds(window: list[MonthWindowItem]) -> tuple[date, date]:
    """Inclusive calendar date range for daily DB queries: [first day, last day]."""
    if not window:
        raise ValueError("window is empty")
    first = window[0]
    last = window[-1]
    if last.month == 12:
        last_day = date(last.year, 12, 31)
    else:
        last_day = date(last.year, last.month + 1, 1) - timedelta(days=1)
    return date(first.year, first.month, 1), last_day


def filter_calendar_to_window(
    calendar: dict[date, bool] | list[CalendarDay],
    window: list[MonthWindowItem],
) -> dict[date, bool]:
    """Keep only days inside the availability table window (Sep 2026–Aug 2027 when fixed)."""
    start, end = window_calendar_bounds(window)
    if isinstance(calendar, dict):
        return {d: v for d, v in calendar.items() if start <= d <= end}
    return {day.date: day.available for day in calendar if start <= day.date <= end}


def build_month_window(start_date: date, months: int = 12) -> list[MonthWindowItem]:
    """Build `months` consecutive calendar months starting at start_date's month."""
    if months < 1:
        raise ValueError("months must be >= 1")
    items: list[MonthWindowItem] = []
    year = start_date.year
    month = start_date.month
    for _ in range(months):
        items.append(
            MonthWindowItem(
                year=year,
                month=month,
                key=f"{year:04d}-{month:02d}",
                display_name=f"{MONTH_ABBR[month - 1]} {year % 100:02d}",
            )
        )
        month += 1
        if month == 13:
            month = 1
            year += 1
    return items
