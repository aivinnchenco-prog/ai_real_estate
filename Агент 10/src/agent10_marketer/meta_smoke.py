"""Controlled Meta PAUSED campaign / ad set smoke helpers (no creative/ad)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from agent10_marketer.adapters.meta_ads import MetaMarketingApiAdapter
from agent10_marketer.adapters.meta_errors import MetaSafetyError
from agent10_marketer.config import MetaConfig

SMOKE_CAMPAIGN_NAME = "OPENHOME_AGENT10_SMOKE_TEST_2026_08_09"
SMOKE_CAMPAIGN_ID = "120252824987200202"
SMOKE_OBJECTIVE = "OUTCOME_ENGAGEMENT"
SMOKE_ADSET_NAME = "OPENHOME_AGENT10_ADSET_SMOKE_2026_08_09"
# Smoke is write-path only — empty special categories (no housing delivery config).
SMOKE_SPECIAL_AD_CATEGORIES: list[str] = []
EXPECTED_AD_ACCOUNT_ID = "3462495317264561"
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

# Confirmed via live account GET: min_daily_budget=3394 minor units ≈ 33.94 THB.
# Use 100 THB/day (within Agent 10 caps and above Meta account minimum).
SMOKE_ADSET_DAILY_BUDGET_THB = 100.0
SMOKE_ADSET_DURATION_DAYS = 3
SMOKE_OPTIMIZATION_GOAL = "POST_ENGAGEMENT"
SMOKE_BILLING_EVENT = "IMPRESSIONS"
SMOKE_DESTINATION_TYPE = "ON_POST"
SMOKE_BID_STRATEGY = "LOWEST_COST_WITHOUT_CAP"
SMOKE_TARGETING: dict[str, Any] = {
    # Thailand Meta youth restriction: age_min must be >= 20 when any non-location
    # targeting defaults apply. Location + age only (no interests/demographics).
    "geo_locations": {"countries": ["TH"]},
    "age_min": 20,
}


@dataclass
class SmokePreflightResult:
    ok: bool
    errors: list[str]
    account: dict[str, Any] | None = None
    campaign: dict[str, Any] | None = None
    housing_status: str = "unknown"

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise MetaSafetyError("SMOKE PREFLIGHT FAIL: " + "; ".join(self.errors))


def _name_matches(actual: str | None, expected: str) -> bool:
    if not actual or not expected:
        return False
    a = actual.strip().lower()
    e = expected.strip().lower()
    return e in a or a in e


def bangkok_schedule(
    *,
    duration_days: int = SMOKE_ADSET_DURATION_DAYS,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Return (start_time, end_time) ISO8601 with Asia/Bangkok offset."""
    base = now.astimezone(BANGKOK_TZ) if now else datetime.now(BANGKOK_TZ)
    start = base + timedelta(minutes=30)
    end = start + timedelta(days=int(duration_days))
    return (
        start.strftime("%Y-%m-%dT%H:%M:%S%z"),
        end.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )


def run_smoke_preflight(
    adapter: MetaMarketingApiAdapter,
    meta: MetaConfig,
    *,
    require_write_enabled: bool = True,
) -> SmokePreflightResult:
    """Abort rules for live PAUSED campaign smoke-test."""
    errors: list[str] = []

    if not meta.token_set:
        errors.append("META_ACCESS_TOKEN missing")
    if require_write_enabled and not meta.write_enabled:
        errors.append("META_WRITE_ENABLED must be true for live smoke")
    if meta.active_enabled:
        errors.append("META_ACTIVE_ENABLED=true — ABORT (ACTIVE safety must stay off)")
    if meta.ad_account_id != EXPECTED_AD_ACCOUNT_ID:
        errors.append(
            f"META_AD_ACCOUNT_ID={meta.ad_account_id} != {EXPECTED_AD_ACCOUNT_ID}"
        )

    account: dict[str, Any] | None = None
    if not errors and meta.token_set:
        account = adapter.get_ad_account()
        if not _name_matches(account.get("name"), meta.expected_account_name):
            errors.append(
                f"account name {account.get('name')!r} != {meta.expected_account_name!r}"
            )
        if (account.get("currency") or "").upper() != meta.expected_currency.upper():
            errors.append(
                f"currency {account.get('currency')!r} != {meta.expected_currency!r}"
            )
        if (account.get("timezone_name") or "") != meta.expected_timezone:
            errors.append(
                f"timezone {account.get('timezone_name')!r} != {meta.expected_timezone!r}"
            )

    return SmokePreflightResult(ok=not errors, errors=errors, account=account)


def run_adset_smoke_preflight(
    adapter: MetaMarketingApiAdapter,
    meta: MetaConfig,
    *,
    campaign_id: str = SMOKE_CAMPAIGN_ID,
    require_write_enabled: bool = True,
) -> SmokePreflightResult:
    """Preflight for PAUSED ad set smoke under the known smoke campaign."""
    base = run_smoke_preflight(
        adapter, meta, require_write_enabled=require_write_enabled
    )
    errors = list(base.errors)
    campaign: dict[str, Any] | None = None
    housing_status = "unknown"

    if not errors:
        if str(campaign_id) != SMOKE_CAMPAIGN_ID:
            errors.append(
                f"wrong campaign blocked: {campaign_id} != {SMOKE_CAMPAIGN_ID}"
            )
        else:
            campaign = adapter.get_campaign(campaign_id)
            raw = adapter._get(  # noqa: SLF001
                f"/{campaign_id}",
                fields=(
                    "id,name,status,effective_status,objective,"
                    "special_ad_categories,special_ad_category"
                ),
            )
            campaign = {
                **campaign,
                "special_ad_categories": raw.get("special_ad_categories"),
                "special_ad_category": raw.get("special_ad_category"),
                "objective": raw.get("objective") or campaign.get("objective"),
            }
            if campaign.get("name") != SMOKE_CAMPAIGN_NAME:
                errors.append(
                    f"campaign name {campaign.get('name')!r} != {SMOKE_CAMPAIGN_NAME!r}"
                )
            if str(campaign.get("status") or "").upper() != "PAUSED":
                errors.append(
                    f"campaign status must be PAUSED, got {campaign.get('status')!r}"
                )
            if str(campaign.get("objective") or "") != SMOKE_OBJECTIVE:
                errors.append(
                    f"campaign objective {campaign.get('objective')!r} != {SMOKE_OBJECTIVE!r}"
                )
            cats = campaign.get("special_ad_categories") or []
            sac = str(campaign.get("special_ad_category") or "NONE")
            if "HOUSING" in {str(c).upper() for c in cats} or sac.upper() == "HOUSING":
                housing_status = (
                    "HOUSING (campaign-level) — demographic targeting forbidden"
                )
            elif cats:
                housing_status = f"campaign_special_ad_categories={cats}"
            else:
                housing_status = f"NONE (special_ad_category={sac})"

            if not meta.page_id:
                errors.append(
                    "META_PAGE_ID required for OUTCOME_ENGAGEMENT promoted_object"
                )

    return SmokePreflightResult(
        ok=not errors,
        errors=errors,
        account=base.account,
        campaign=campaign,
        housing_status=housing_status,
    )


def find_campaigns_by_exact_name(
    adapter: MetaMarketingApiAdapter,
    name: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return [c for c in adapter.list_campaigns(limit=limit) if c.get("name") == name]


def find_adsets_by_exact_name(
    adapter: MetaMarketingApiAdapter,
    name: str,
    *,
    campaign_id: str = SMOKE_CAMPAIGN_ID,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = adapter.get_adsets(campaign_id=campaign_id, limit=limit)
    return [r for r in rows if r.get("name") == name]


def create_paused_smoke_campaign(
    adapter: MetaMarketingApiAdapter,
    *,
    name: str = SMOKE_CAMPAIGN_NAME,
    objective: str = SMOKE_OBJECTIVE,
) -> dict[str, Any]:
    """Single PAUSED campaign create — no budget/adset/creative/ad."""
    return adapter.create_campaign(
        name=name,
        objective=objective,
        status="PAUSED",
        special_ad_categories=list(SMOKE_SPECIAL_AD_CATEGORIES),
        is_adset_budget_sharing_enabled=False,
    )


def create_paused_smoke_adset(
    adapter: MetaMarketingApiAdapter,
    meta: MetaConfig,
    *,
    campaign_id: str = SMOKE_CAMPAIGN_ID,
    name: str = SMOKE_ADSET_NAME,
    daily_budget: float = SMOKE_ADSET_DAILY_BUDGET_THB,
    duration_days: int = SMOKE_ADSET_DURATION_DAYS,
) -> dict[str, Any]:
    """Single PAUSED ad set under smoke campaign — no creative/ad."""
    if str(campaign_id) != SMOKE_CAMPAIGN_ID:
        raise MetaSafetyError(
            f"wrong campaign blocked: {campaign_id} != {SMOKE_CAMPAIGN_ID}"
        )
    start_time, end_time = bangkok_schedule(duration_days=duration_days)
    return adapter.create_adset(
        name=name,
        campaign_id=campaign_id,
        status="PAUSED",
        daily_budget=daily_budget,
        duration_days=duration_days,
        billing_event=SMOKE_BILLING_EVENT,
        optimization_goal=SMOKE_OPTIMIZATION_GOAL,
        destination_type=SMOKE_DESTINATION_TYPE,
        bid_strategy=SMOKE_BID_STRATEGY,
        promoted_object={"page_id": str(meta.page_id)},
        targeting=dict(SMOKE_TARGETING),
        start_time=start_time,
        end_time=end_time,
    )


def get_campaign(adapter: MetaMarketingApiAdapter, campaign_id: str) -> dict[str, Any]:
    return adapter.get_campaign(campaign_id)


def get_adset(adapter: MetaMarketingApiAdapter, adset_id: str) -> dict[str, Any]:
    return adapter.get_adset(adset_id)


# ── Facebook existing-post creative smoke ───────────────────────────────

SMOKE_FB_CREATIVE_NAME = "OPENHOME_AGENT10_FB_CREATIVE_SMOKE_2026_08_09"
SMOKE_ADSET_ID = "120252828754070202"


def find_adcreatives_by_exact_name(
    adapter: MetaMarketingApiAdapter,
    name: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return [c for c in adapter.list_adcreatives(limit=limit) if c.get("name") == name]


def create_paused_smoke_fb_creative(
    adapter: MetaMarketingApiAdapter,
    *,
    object_story_id: str,
    name: str = SMOKE_FB_CREATIVE_NAME,
) -> dict[str, Any]:
    """Single Ad Creative from confirmed existing Facebook post — no Ad."""
    if not object_story_id or not str(object_story_id).strip():
        raise MetaSafetyError("no confirmed FB post → no creative POST")
    confirmed = adapter.confirm_facebook_object_story_id(object_story_id)
    return adapter.create_creative_from_existing_facebook_post(
        name=name,
        object_story_id=confirmed,
    )


def audit_instagram_existing_post(meta: MetaConfig) -> dict[str, Any]:
    """Read-only Instagram readiness audit (no POST)."""
    return {
        "status": "BLOCKED",
        "configured_instagram_account_id": meta.instagram_account_id or None,
        "page_instagram_business_account": None,
        "publication_ledger_instagram_rows": 0,
        "reasons": [
            "Page.instagram_business_account is not linked/visible for current token",
            f"GET /{meta.instagram_account_id or 'IG_ID'} fails (missing permissions / unsupported)",
            "Publication Ledger currently has 0 Instagram rows in local DB",
            "create_creative_from_existing_instagram_media remains stub until official Graph contract confirmed",
        ],
        "future_requirements": [
            "Link Instagram Professional account @open.home.th to Facebook Page OpenHome",
            "Token needs instagram_basic / instagram_manage_insights (or current Meta equivalent) + ads_management",
            "Obtain Instagram media id (IG media Graph id), not just permalink",
            "Confirm Marketing API creative fields for existing IG media on Graph version in use",
            "Store mapping in Publication Ledger: object_id ↔ instagram_media_id ↔ permalink",
        ],
    }
