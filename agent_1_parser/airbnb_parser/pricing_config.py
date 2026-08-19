"""Конфигурация monthly pricing: env → pipeline.json → defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import config
from agent2_handoff import find_agent2_root, price_months_ahead


def _pipeline_cfg() -> dict[str, Any]:
    root = find_agent2_root()
    if not root:
        return {}
    try:
        return json.loads((root / "config" / "pipeline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _monthly_section() -> dict[str, Any]:
    return _pipeline_cfg().get("monthly_pricing") or {}


def months_ahead() -> int:
    return max(1, price_months_ahead(find_agent2_root()))


def background_workers() -> int:
    env = str(getattr(config, "PRICE_BACKGROUND_WORKERS", "") or "").strip()
    if env:
        return max(1, int(env))
    return max(1, int(_monthly_section().get("background_workers", 1)))


def request_delay_seconds() -> tuple[float, float]:
    section = _monthly_section().get("background_request_delay_seconds") or {}
    env_min = str(getattr(config, "PRICE_BACKGROUND_DELAY_MIN", "") or "").strip()
    env_max = str(getattr(config, "PRICE_BACKGROUND_DELAY_MAX", "") or "").strip()
    lo = float(env_min if env_min else section.get("min", 5))
    hi = float(env_max if env_max else section.get("max", 12))
    if hi < lo:
        hi = lo
    return lo, hi


def block_cooldown_seconds(streak: int) -> float:
    tiers = _monthly_section().get("block_cooldown_seconds") or [300, 900, 3600]
    if not tiers:
        tiers = [300, 900, 3600]
    idx = min(max(0, streak), len(tiers) - 1)
    return float(tiers[idx])


def retry_delay_seconds(attempt: int) -> float:
    tiers = _monthly_section().get("retry_delay_seconds") or [120, 600, 1800]
    if not tiers:
        tiers = [120, 600, 1800]
    idx = min(max(0, attempt - 1), len(tiers) - 1)
    return float(tiers[idx])


def max_job_attempts() -> int:
    return max(1, int(_monthly_section().get("max_job_attempts", 3)))


def stale_running_seconds() -> float:
    return float(_monthly_section().get("stale_running_seconds", 300))


def calendar_snapshot_ttl_seconds() -> float:
    return float(_monthly_section().get("calendar_snapshot_ttl_seconds", 86400))


def queue_path() -> Path:
    raw = getattr(config, "PRICE_QUEUE_PATH", "data/pricing_queue.json")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path
