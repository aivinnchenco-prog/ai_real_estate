"""Worker profile path helpers — separate Playwright calendar vs Selenium pricing namespaces."""

from __future__ import annotations

from pathlib import Path

from .models import AirbnbWorker

CALENDAR_SUBDIR = "calendar"
PRICING_SUBDIR = "pricing"


def worker_root_profile(worker: AirbnbWorker) -> Path:
    return Path(worker.profile_path)


def calendar_profile_path(worker: AirbnbWorker) -> Path:
    """Playwright calendar storage lock directory (storage_state.json lives here)."""
    return worker_root_profile(worker)


def calendar_storage_path(worker: AirbnbWorker) -> Path:
    return calendar_profile_path(worker) / "storage_state.json"


def pricing_profile_path(worker: AirbnbWorker) -> Path:
    """Selenium persistent user-data-dir — never shared with Playwright runtime."""
    return worker_root_profile(worker) / PRICING_SUBDIR
