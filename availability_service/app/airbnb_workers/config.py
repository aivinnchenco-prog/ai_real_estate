from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..config import PACKAGE_ROOT, resolve_runtime_dir
from .models import ProxyConfig

DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]


@dataclass(frozen=True)
class AirbnbWorkerPoolConfig:
    enabled: bool
    proxy_pool_path: Path
    user_agents_path: Path | None
    profiles_root: Path
    max_concurrency: int
    min_delay_seconds: float
    max_delay_seconds: float
    rebalance_threshold: int
    captcha_cooldown_minutes: int
    captcha_quarantine_threshold: int
    calendar_timeout_seconds: int
    pricing_worker_pool_enabled: bool
    pricing_min_delay_seconds: float
    pricing_max_delay_seconds: float


def _default_proxy_pool_path(runtime_dir: Path) -> Path:
    env = os.environ.get("AIRBNB_PROXY_POOL_JSON", "").strip()
    if env:
        return Path(env).expanduser()
    prod = runtime_dir / "airbnb_proxy_pool.json"
    if prod.exists():
        return prod
    local = PACKAGE_ROOT / "config" / "airbnb_proxy_pool.json"
    return local


def _default_user_agents_path(runtime_dir: Path) -> Path | None:
    env = os.environ.get("AIRBNB_USER_AGENTS_JSON", "").strip()
    if env:
        return Path(env).expanduser()
    prod = runtime_dir / "airbnb_user_agents.json"
    if prod.exists():
        return prod
    local = PACKAGE_ROOT / "config" / "airbnb_user_agents.json"
    return local if local.exists() else None


def load_worker_pool_config() -> AirbnbWorkerPoolConfig:
    runtime_dir = resolve_runtime_dir()
    proxy_path = _default_proxy_pool_path(runtime_dir)
    enabled_raw = os.environ.get("AIRBNB_WORKER_POOL_ENABLED", "").strip().lower()
    if enabled_raw in {"", "auto"}:
        enabled = proxy_path.exists()
    else:
        enabled = enabled_raw in {"1", "true", "yes", "on"}
    profiles_root = Path(
        os.environ.get("AIRBNB_WORKER_PROFILES_ROOT", "").strip()
        or str(runtime_dir / "browser_profiles" / "airbnb_availability")
    ).expanduser()
    return AirbnbWorkerPoolConfig(
        enabled=enabled,
        proxy_pool_path=proxy_path,
        user_agents_path=_default_user_agents_path(runtime_dir),
        profiles_root=profiles_root,
        max_concurrency=max(1, int(os.environ.get("AIRBNB_WORKER_MAX_CONCURRENCY") or "1")),
        min_delay_seconds=float(os.environ.get("AIRBNB_WORKER_MIN_DELAY_SECONDS") or "8"),
        max_delay_seconds=float(os.environ.get("AIRBNB_WORKER_MAX_DELAY_SECONDS") or "18"),
        rebalance_threshold=max(1, int(os.environ.get("AIRBNB_WORKER_REBALANCE_THRESHOLD") or "3")),
        captcha_cooldown_minutes=max(1, int(os.environ.get("AIRBNB_CAPTCHA_COOLDOWN_MINUTES") or "30")),
        captcha_quarantine_threshold=max(
            1, int(os.environ.get("AIRBNB_CAPTCHA_QUARANTINE_THRESHOLD") or "3")
        ),
        calendar_timeout_seconds=max(15, int(os.environ.get("AIRBNB_CALENDAR_TIMEOUT_SECONDS") or "60")),
        pricing_worker_pool_enabled=_pricing_worker_pool_enabled(),
        pricing_min_delay_seconds=float(
            os.environ.get("AIRBNB_PRICING_MIN_DELAY_SECONDS")
            or os.environ.get("AIRBNB_WORKER_MIN_DELAY_SECONDS")
            or "8"
        ),
        pricing_max_delay_seconds=float(
            os.environ.get("AIRBNB_PRICING_MAX_DELAY_SECONDS")
            or os.environ.get("AIRBNB_WORKER_MAX_DELAY_SECONDS")
            or "18"
        ),
    )


def _pricing_worker_pool_enabled() -> bool:
    raw = os.environ.get("AIRBNB_PRICING_WORKER_POOL_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def pricing_worker_pool_enabled() -> bool:
    """Phase 2: route Selenium pricing through worker identity (independent rollback flag)."""
    pool_config = load_worker_pool_config()
    return pool_config.enabled and pool_config.pricing_worker_pool_enabled


def load_proxy_pool(path: Path) -> list[ProxyConfig]:
    if not path.exists():
        raise FileNotFoundError(f"proxy pool not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[ProxyConfig] = []
    for item in raw:
        out.append(
            ProxyConfig(
                id=str(item["id"]),
                server=str(item["server"]),
                username=str(item["username"]),
                password=str(item["password"]),
            )
        )
    return out


def load_user_agents(path: Path | None) -> list[str]:
    if path and path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list) and raw:
            return [str(x) for x in raw]
    return list(DEFAULT_USER_AGENTS)


def mask_proxy_server(server: str) -> str:
    """Safe log representation — no credentials."""
    return server.split("@")[-1] if "@" in server else server
