from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import RefreshTier, WindowStartMode

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PROD_ENV = Path("/opt/openhome/.env")
PROD_RUNTIME_DIR = Path("/opt/openhome/runtime/availability")
LOCAL_RUNTIME_DIR = PACKAGE_ROOT / "data"
_LOADED_ENV_SOURCES: list[Path] = []


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))
    _LOADED_ENV_SOURCES.append(path)


def load_project_env() -> None:
    global _LOADED_ENV_SOURCES
    _LOADED_ENV_SOURCES = []
    if CANONICAL_PROD_ENV.exists():
        _load_dotenv(CANONICAL_PROD_ENV)
    else:
        _load_dotenv(PROJECT_ROOT / ".env")
    _load_dotenv(PACKAGE_ROOT / ".env")


def canonical_env_source() -> str:
    """Primary production env path when present; otherwise project .env."""
    if CANONICAL_PROD_ENV.exists():
        return str(CANONICAL_PROD_ENV)
    project_env = PROJECT_ROOT / ".env"
    if project_env.exists():
        return str(project_env)
    return ""


def loaded_env_sources() -> list[str]:
    return [str(path) for path in _LOADED_ENV_SOURCES]


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_tier(raw: str | None) -> RefreshTier:
    value = (raw or RefreshTier.H48.value).strip().upper()
    aliases = {
        "FIRST_REFRESH": RefreshTier.FIRST_REFRESH,
        "1H": RefreshTier.H1,
        "12H": RefreshTier.H12,
        "24H": RefreshTier.H24,
        "48H": RefreshTier.H48,
        "78H": RefreshTier.H78,
    }
    if value not in aliases:
        raise ValueError(f"Unsupported refresh tier: {raw!r}")
    return aliases[value]


def _parse_window_start_mode(raw: str | None) -> WindowStartMode:
    value = (raw or WindowStartMode.SEP_2026_AUG_2027.value).strip().lower()
    aliases = {
        WindowStartMode.CURRENT_MONTH.value: WindowStartMode.CURRENT_MONTH,
        WindowStartMode.NEXT_MONTH.value: WindowStartMode.NEXT_MONTH,
        WindowStartMode.SEP_2026_AUG_2027.value: WindowStartMode.SEP_2026_AUG_2027,
        "fixed_sep_2026": WindowStartMode.SEP_2026_AUG_2027,
        "sep_2026": WindowStartMode.SEP_2026_AUG_2027,
    }
    if value not in aliases:
        raise ValueError(f"Unsupported window start mode: {raw!r}")
    return aliases[value]


@dataclass(frozen=True)
class AvailabilityConfig:
    enabled: bool
    dry_run: bool
    notion_api_key: str
    source_database_id: str
    target_database_id: str
    target_data_source_id: str
    runtime_dir: Path
    sqlite_path: Path
    default_refresh_tier: RefreshTier
    max_concurrency: int
    dry_run_batch_size: int
    object_concurrency: int
    calendar_concurrency: int
    price_concurrency: int
    browser_max_instances: int
    batch_safe_max_objects: int
    window_start_mode: WindowStartMode
    airbnb_enabled: bool = False
    facebook_enabled: bool = False
    facebook_browser_profile: Path = Path("/opt/openhome/runtime/browser_profiles/facebook_owner_outreach")
    price_object_max_seconds: int = 600

    @property
    def writes_allowed(self) -> bool:
        return self.enabled and not self.dry_run

    @property
    def airbnb_live_allowed(self) -> bool:
        return self.enabled and self.airbnb_enabled and not self.dry_run

    @property
    def facebook_live_allowed(self) -> bool:
        return self.enabled and self.facebook_enabled and not self.dry_run

    def masked(self) -> dict[str, str | bool | int]:
        key = self.notion_api_key
        return {
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "notion_api_key": f"ntn_***{key[-4:]}" if len(key) >= 8 else "(missing)",
            "source_database_id": self.source_database_id,
            "target_database_id": self.target_database_id,
            "target_data_source_id": self.target_data_source_id or "(none)",
            "runtime_dir": str(self.runtime_dir),
            "sqlite_path": str(self.sqlite_path),
            "default_refresh_tier": self.default_refresh_tier.value,
            "max_concurrency": self.max_concurrency,
            "dry_run_batch_size": self.dry_run_batch_size,
            "object_concurrency": self.object_concurrency,
            "calendar_concurrency": self.calendar_concurrency,
            "price_concurrency": self.price_concurrency,
            "browser_max_instances": self.browser_max_instances,
            "batch_safe_max_objects": self.batch_safe_max_objects,
            "price_object_max_seconds": self.price_object_max_seconds,
            "window_start_mode": self.window_start_mode.value,
            "airbnb_enabled": self.airbnb_enabled,
            "airbnb_live_allowed": self.airbnb_live_allowed,
            "facebook_enabled": self.facebook_enabled,
            "facebook_live_allowed": self.facebook_live_allowed,
            "facebook_browser_profile": str(self.facebook_browser_profile),
        }


def resolve_runtime_dir(raw: str | None = None) -> Path:
    if raw:
        return Path(raw).expanduser()
    env_raw = os.environ.get("AVAILABILITY_RUNTIME_DIR", "").strip()
    if env_raw:
        return Path(env_raw).expanduser()
    if Path("/opt/openhome").exists():
        return PROD_RUNTIME_DIR
    return LOCAL_RUNTIME_DIR


def load_config() -> AvailabilityConfig:
    load_project_env()
    runtime_dir = resolve_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    source_id = (
        os.environ.get("AVAILABILITY_SOURCE_NOTION_DATABASE_ID")
        or os.environ.get("NOTION_DB_ID")
        or os.environ.get("NOTION_DATABASE_ID")
        or ""
    ).strip()
    target_id = os.environ.get("AVAILABILITY_TARGET_NOTION_DATABASE_ID", "").strip()
    target_ds = os.environ.get("AVAILABILITY_TARGET_NOTION_DATA_SOURCE_ID", "").strip()
    token = (
        os.environ.get("AVAILABILITY_NOTION_API_KEY")
        or os.environ.get("NOTION_DOCS_API_KEY")
        or os.environ.get("NOTION_API_KEY")
        or os.environ.get("NOTION_TOKEN")
        or ""
    ).strip()
    return AvailabilityConfig(
        enabled=_as_bool(os.environ.get("AVAILABILITY_ENABLED"), False),
        dry_run=_as_bool(os.environ.get("AVAILABILITY_DRY_RUN"), True),
        notion_api_key=token,
        source_database_id=source_id,
        target_database_id=target_id,
        target_data_source_id=target_ds,
        runtime_dir=runtime_dir,
        sqlite_path=runtime_dir / "availability.sqlite3",
        default_refresh_tier=_parse_tier(os.environ.get("AVAILABILITY_DEFAULT_REFRESH_TIER")),
        max_concurrency=max(1, int(os.environ.get("AVAILABILITY_MAX_CONCURRENCY") or "2")),
        dry_run_batch_size=max(1, int(os.environ.get("AVAILABILITY_DRY_RUN_BATCH_SIZE") or "25")),
        object_concurrency=max(
            1, int(os.environ.get("AVAILABILITY_OBJECT_CONCURRENCY") or "1")
        ),
        calendar_concurrency=max(
            1, int(os.environ.get("AVAILABILITY_CALENDAR_CONCURRENCY") or "1")
        ),
        price_concurrency=max(
            1, int(os.environ.get("AVAILABILITY_PRICE_CONCURRENCY") or "1")
        ),
        browser_max_instances=max(
            1, int(os.environ.get("AVAILABILITY_BROWSER_MAX_INSTANCES") or "2")
        ),
        batch_safe_max_objects=max(
            1, int(os.environ.get("AVAILABILITY_BATCH_SAFE_MAX_OBJECTS") or "5")
        ),
        window_start_mode=_parse_window_start_mode(
            os.environ.get("AVAILABILITY_WINDOW_START_MODE")
        ),
        airbnb_enabled=_as_bool(os.environ.get("AVAILABILITY_AIRBNB_ENABLED"), False),
        facebook_enabled=_as_bool(os.environ.get("AVAILABILITY_FACEBOOK_ENABLED"), False),
        facebook_browser_profile=Path(
            os.environ.get("FB_BROWSER_PROFILE", "").strip()
            or "/opt/openhome/runtime/browser_profiles/facebook_owner_outreach"
        ).expanduser(),
        price_object_max_seconds=max(
            0, int(os.environ.get("AVAILABILITY_PRICE_OBJECT_MAX_SECONDS") or "600")
        ),
    )
