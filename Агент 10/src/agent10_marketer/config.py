"""Configuration loader for Agent 10 (no imports from Agents 1–9)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PACKAGE_ROOT / "config"
DEFAULT_DATA_DIR = PACKAGE_ROOT / "data"

# Canonical Open Home Meta account (local contract; token never stored here).
DEFAULT_META_APP_ID = "1051487844031310"
DEFAULT_META_BUSINESS_ID = "1572755037543832"
DEFAULT_META_AD_ACCOUNT_ID = "3462495317264561"
DEFAULT_META_PAGE_ID = "1189108177625326"
DEFAULT_META_INSTAGRAM_ACCOUNT_ID = "17841410402639080"
DEFAULT_META_GRAPH_API_VERSION = "v26.0"
DEFAULT_META_API_BASE_URL = "https://graph.facebook.com"
DEFAULT_META_EXPECTED_ACCOUNT_NAME = "Open Home th"
DEFAULT_META_EXPECTED_CURRENCY = "THB"
DEFAULT_META_EXPECTED_TIMEZONE = "Asia/Bangkok"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip()


def normalize_ad_account_id(raw: str) -> str:
    """Return bare ad account id without act_ prefix."""
    text = (raw or "").strip()
    if text.lower().startswith("act_"):
        return text[4:]
    return text


@dataclass(frozen=True)
class BudgetConfig:
    currency: str = "THB"
    default_daily_budget: float = 500.0
    max_daily_budget: float = 1500.0
    default_duration_days: int = 5
    max_duration_days: int = 14
    max_total_budget: float = 2500.0
    default_objective: str = "OUTCOME_ENGAGEMENT"
    default_strategy: str = "boost_existing_reel"
    default_placements_mode: str = "advantage_plus"
    default_audience_mode: str = "advantage_plus"
    max_budget_change_pct: float = 20.0


@dataclass(frozen=True)
class MetaConfig:
    """Meta Marketing API credentials and safety switches.

    META_ACCESS_TOKEN is read from env only — never logged or committed.
    META_APP_SECRET is optional and not required for server-to-server calls.
    """

    app_id: str = DEFAULT_META_APP_ID
    business_id: str = DEFAULT_META_BUSINESS_ID
    ad_account_id: str = DEFAULT_META_AD_ACCOUNT_ID
    page_id: str = DEFAULT_META_PAGE_ID
    instagram_account_id: str = DEFAULT_META_INSTAGRAM_ACCOUNT_ID
    access_token: str = ""
    graph_api_version: str = DEFAULT_META_GRAPH_API_VERSION
    api_base_url: str = DEFAULT_META_API_BASE_URL
    app_secret: str = ""  # optional; unused in V1 read/write path
    write_enabled: bool = False
    active_enabled: bool = False
    ads_enabled: bool = False
    expected_account_name: str = DEFAULT_META_EXPECTED_ACCOUNT_NAME
    expected_currency: str = DEFAULT_META_EXPECTED_CURRENCY
    expected_timezone: str = DEFAULT_META_EXPECTED_TIMEZONE
    diagnostic_strict: bool = False
    special_ad_categories: tuple[str, ...] = ("HOUSING",)
    allowed_objectives: tuple[str, ...] = (
        "OUTCOME_ENGAGEMENT",
        "OUTCOME_TRAFFIC",
        "OUTCOME_LEADS",
        "OUTCOME_AWARENESS",
        "OUTCOME_SALES",
        "OUTCOME_APP_PROMOTION",
    )
    request_timeout_sec: float = 30.0
    get_max_retries: int = 2

    @property
    def act_id(self) -> str:
        return f"act_{normalize_ad_account_id(self.ad_account_id)}"

    @property
    def token_set(self) -> bool:
        return bool(self.access_token.strip())

    @property
    def graph_root(self) -> str:
        base = self.api_base_url.rstrip("/")
        version = self.graph_api_version.strip().lstrip("/")
        return f"{base}/{version}"

    def redacted_dict(self) -> dict[str, Any]:
        """Safe summary for diagnostics / logs (token never included)."""
        return {
            "app_id": self.app_id,
            "business_id": self.business_id,
            "ad_account_id": normalize_ad_account_id(self.ad_account_id),
            "page_id": self.page_id,
            "instagram_account_id": self.instagram_account_id,
            "graph_api_version": self.graph_api_version,
            "api_base_url": self.api_base_url,
            "token": "SET" if self.token_set else "MISSING",
            "write_enabled": self.write_enabled,
            "active_enabled": self.active_enabled,
            "ads_enabled": self.ads_enabled,
            "expected_account_name": self.expected_account_name,
            "expected_currency": self.expected_currency,
            "expected_timezone": self.expected_timezone,
            "diagnostic_strict": self.diagnostic_strict,
        }


@dataclass(frozen=True)
class ScoringConfig:
    min_age_hours: float = 1.0
    windows_hours: dict[str, int] = field(
        default_factory=lambda: {"24h": 24, "72h": 72, "7d": 168}
    )
    default_window: str = "72h"
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "save_rate": 0.28,
            "comment_rate": 0.18,
            "share_rate": 0.14,
            "engagement_rate": 0.20,
            "reach_velocity": 0.12,
            "view_velocity": 0.08,
        }
    )
    rate_denominator_priority: list[str] = field(
        default_factory=lambda: ["impressions", "reach", "views"]
    )
    velocity_metric_map: dict[str, str] = field(
        default_factory=lambda: {
            "reach_velocity": "reach",
            "view_velocity": "views",
        }
    )


@dataclass(frozen=True)
class Agent10Config:
    config_dir: Path
    data_dir: Path
    scoring: ScoringConfig
    budget: BudgetConfig
    meta: MetaConfig = field(default_factory=MetaConfig)
    notion_token: str = ""
    notion_database_id: str = ""
    postmypost_api_token: str = ""
    postmypost_base_url: str = "https://api.postmypost.io/v4.1"
    meta_ads_enabled: bool = False
    llm_enabled: bool = False
    llm_api_key: str = ""
    llm_model: str = ""
    llm_provider: str = ""


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_csv_tuple(raw: str, default: tuple[str, ...]) -> tuple[str, ...]:
    text = (raw or "").strip()
    if not text:
        return default
    parts = [p.strip() for p in text.split(",") if p.strip()]
    return tuple(parts) if parts else default


def load_scoring_config(config_dir: Path | None = None) -> ScoringConfig:
    config_dir = config_dir or Path(os.getenv("AGENT10_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    raw = _load_json(config_dir / "scoring_weights.json")
    weights = dict(raw.get("weights") or {})
    windows = dict(raw.get("windows_hours") or {"24h": 24, "72h": 72, "7d": 168})
    return ScoringConfig(
        min_age_hours=_env_float("AGENT10_MIN_AGE_HOURS", float(raw.get("min_age_hours", 1.0))),
        windows_hours={str(k): int(v) for k, v in windows.items()},
        default_window=str(raw.get("default_window") or "72h"),
        weights={str(k): float(v) for k, v in weights.items()} or ScoringConfig().weights,
        rate_denominator_priority=list(
            raw.get("rate_denominator_priority") or ["impressions", "reach", "views"]
        ),
        velocity_metric_map=dict(
            raw.get("velocity_metric_map")
            or {"reach_velocity": "reach", "view_velocity": "views"}
        ),
    )


def load_budget_config(config_dir: Path | None = None) -> BudgetConfig:
    config_dir = config_dir or Path(os.getenv("AGENT10_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    raw = _load_json(config_dir / "budget.json")
    return BudgetConfig(
        currency=str(raw.get("currency") or "THB"),
        default_daily_budget=_env_float(
            "TARGET_DEFAULT_DAILY_BUDGET", float(raw.get("default_daily_budget", 500))
        ),
        max_daily_budget=_env_float(
            "TARGET_MAX_DAILY_BUDGET", float(raw.get("max_daily_budget", 1500))
        ),
        default_duration_days=_env_int(
            "TARGET_DEFAULT_DURATION_DAYS", int(raw.get("default_duration_days", 5))
        ),
        max_duration_days=_env_int(
            "TARGET_MAX_DURATION_DAYS", int(raw.get("max_duration_days", 14))
        ),
        max_total_budget=_env_float(
            "TARGET_MAX_TOTAL_BUDGET", float(raw.get("max_total_budget", 2500))
        ),
        default_objective=str(raw.get("default_objective") or "OUTCOME_ENGAGEMENT"),
        default_strategy=str(raw.get("default_strategy") or "boost_existing_reel"),
        default_placements_mode=str(raw.get("default_placements_mode") or "advantage_plus"),
        default_audience_mode=str(raw.get("default_audience_mode") or "advantage_plus"),
        max_budget_change_pct=_env_float(
            "TARGET_MAX_BUDGET_CHANGE_PCT",
            float(raw.get("max_budget_change_pct", 20.0)),
        ),
    )


def load_meta_config() -> MetaConfig:
    categories = _parse_csv_tuple(
        _env_str("META_SPECIAL_AD_CATEGORIES", "HOUSING"),
        ("HOUSING",),
    )
    objectives = _parse_csv_tuple(
        _env_str("META_ALLOWED_OBJECTIVES", ""),
        MetaConfig().allowed_objectives,
    )
    return MetaConfig(
        app_id=_env_str("META_APP_ID", DEFAULT_META_APP_ID),
        business_id=_env_str("META_BUSINESS_ID", DEFAULT_META_BUSINESS_ID),
        ad_account_id=normalize_ad_account_id(
            _env_str("META_AD_ACCOUNT_ID", DEFAULT_META_AD_ACCOUNT_ID)
        ),
        page_id=_env_str("META_PAGE_ID", DEFAULT_META_PAGE_ID),
        instagram_account_id=_env_str(
            "META_INSTAGRAM_ACCOUNT_ID", DEFAULT_META_INSTAGRAM_ACCOUNT_ID
        ),
        access_token=_env_str("META_ACCESS_TOKEN", ""),
        graph_api_version=_env_str("META_GRAPH_API_VERSION", DEFAULT_META_GRAPH_API_VERSION),
        api_base_url=_env_str("META_API_BASE_URL", DEFAULT_META_API_BASE_URL).rstrip("/"),
        app_secret=_env_str("META_APP_SECRET", ""),  # optional; unused
        write_enabled=_env_bool("META_WRITE_ENABLED", False),
        active_enabled=_env_bool("META_ACTIVE_ENABLED", False),
        ads_enabled=_env_bool("META_ADS_ENABLED", False),
        expected_account_name=_env_str(
            "META_EXPECTED_ACCOUNT_NAME", DEFAULT_META_EXPECTED_ACCOUNT_NAME
        ),
        expected_currency=_env_str(
            "META_EXPECTED_CURRENCY", DEFAULT_META_EXPECTED_CURRENCY
        ),
        expected_timezone=_env_str(
            "META_EXPECTED_TIMEZONE", DEFAULT_META_EXPECTED_TIMEZONE
        ),
        diagnostic_strict=_env_bool("META_DIAGNOSTIC_STRICT", False),
        special_ad_categories=categories,
        allowed_objectives=objectives,
        request_timeout_sec=_env_float("META_REQUEST_TIMEOUT_SEC", 30.0),
        get_max_retries=_env_int("META_GET_MAX_RETRIES", 2),
    )


def load_config(
    *,
    config_dir: Path | None = None,
    data_dir: Path | None = None,
) -> Agent10Config:
    config_dir = Path(config_dir or os.getenv("AGENT10_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    data_dir = Path(data_dir or os.getenv("AGENT10_DATA_DIR") or DEFAULT_DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    token = (
        os.getenv("POSTMYPOST_API_TOKEN")
        or os.getenv("POSTMYPOST_TOKEN")
        or ""
    )
    meta = load_meta_config()
    return Agent10Config(
        config_dir=config_dir,
        data_dir=data_dir,
        scoring=load_scoring_config(config_dir),
        budget=load_budget_config(config_dir),
        meta=meta,
        notion_token=os.getenv("AGENT10_NOTION_TOKEN") or os.getenv("NOTION_API_KEY") or "",
        notion_database_id=(
            os.getenv("AGENT10_NOTION_DATABASE_ID") or os.getenv("NOTION_DB_ID") or ""
        ),
        postmypost_api_token=token,
        postmypost_base_url=(
            os.getenv("POSTMYPOST_BASE_URL") or "https://api.postmypost.io/v4.1"
        ).rstrip("/"),
        meta_ads_enabled=meta.ads_enabled,
        llm_enabled=_env_bool("AGENT10_LLM_ENABLED", False),
        llm_api_key=os.getenv("AGENT10_LLM_API_KEY") or "",
        llm_model=os.getenv("AGENT10_LLM_MODEL") or "",
        llm_provider=os.getenv("AGENT10_LLM_PROVIDER") or "",
    )
