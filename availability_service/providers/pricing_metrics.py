"""Pricing fetch counters (Availability layer; Agent1 semantics unchanged)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PriceFetchMetrics:
    months_requested: int = 0
    fetch_calls: int = 0
    page_loads: int = 0
    failed_fetch_calls: int = 0
    successful_months: int = 0
    browser_inits: int = 0
    price_no_price_results: int = 0
    price_timeout_results: int = 0
    price_access_denied_results: int = 0
    price_browser_error_results: int = 0
    price_other_transient_results: int = 0
    price_object_wallclock_capped: bool = False

    @property
    def attempts_total(self) -> int:
        return self.fetch_calls

    @property
    def internal_retries(self) -> int:
        return max(0, self.page_loads - self.fetch_calls)

    def record_fetch_result(self, value: float | None) -> None:
        if value is None:
            self.failed_fetch_calls += 1

    def record_month_entries(self, entries: dict[str, dict]) -> None:
        self.successful_months = sum(
            1 for entry in entries.values() if entry and entry.get("price")
        )
