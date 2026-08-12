"""Meta Ads adapter — the ONLY paid-ads integration for Agent 10.

See SCOPE.md: no Google/TikTok/other ad-network adapters.

Implementations:
- DisabledMetaAdsAdapter / MockMetaAdsAdapter (offline)
- MetaMarketingApiAdapter (real Graph / Marketing API over HTTP)
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from agent10_marketer.adapters.meta_errors import (
    MetaActiveDisabled,
    MetaApiError,
    MetaApprovalRequired,
    MetaNetworkError,
    MetaPermissionDenied,
    MetaSafetyError,
    MetaServerError,
    MetaTokenMissing,
    MetaUnsupportedField,
    classify_meta_error,
)
from agent10_marketer.adapters.meta_insights import (
    normalize_account,
    normalize_campaign,
    normalize_insights,
)
from agent10_marketer.adapters.meta_policy import (
    FORCED_CREATE_STATUS,
    assert_budget_within_caps,
    assert_objective_allowed,
    assert_write_enabled,
    force_paused_status,
    thb_to_meta_minor_units,
    validate_object_story_id,
)
from agent10_marketer.config import BudgetConfig, MetaConfig, load_budget_config, load_meta_config
from agent10_marketer.models import ApprovalState, PaidPerformanceMetrics

logger = logging.getLogger(__name__)

ACCOUNT_FIELDS = "id,name,account_status,currency,timezone_name"
CAMPAIGN_FIELDS = "id,name,status,effective_status"
INSIGHT_FIELDS = "spend,impressions,reach,clicks,ctr,cpc,cpm"
ADSET_FIELDS = "id,name,status,effective_status,daily_budget,campaign_id"
AD_FIELDS = "id,name,status,effective_status,adset_id,campaign_id"


class MetaIntegrationDisabled(RuntimeError):
    """Raised when Meta Marketing API integration is disabled (V1 default)."""


class MetaAdsAdapter(ABC):
    @abstractmethod
    def create_campaign(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def create_adset(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def create_creative_from_existing_post(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def create_ad(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_insights(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def pause_ad(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def resume_ad(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError


class DisabledMetaAdsAdapter(MetaAdsAdapter):
    """Blocks all launch operations. Default for V1."""

    def __init__(self, reason: str = "Meta integration disabled"):
        self.reason = reason
        self.network_calls = 0

    def _block(self, op: str) -> dict[str, Any]:
        raise MetaIntegrationDisabled(f"{self.reason}: {op}")

    def create_campaign(self, **kwargs: Any) -> dict[str, Any]:
        return self._block("create_campaign")

    def create_adset(self, **kwargs: Any) -> dict[str, Any]:
        return self._block("create_adset")

    def create_creative_from_existing_post(self, **kwargs: Any) -> dict[str, Any]:
        return self._block("create_creative_from_existing_post")

    def create_ad(self, **kwargs: Any) -> dict[str, Any]:
        return self._block("create_ad")

    def get_insights(self, **kwargs: Any) -> dict[str, Any]:
        return self._block("get_insights")

    def pause_ad(self, **kwargs: Any) -> dict[str, Any]:
        return self._block("pause_ad")

    def resume_ad(self, **kwargs: Any) -> dict[str, Any]:
        return self._block("resume_ad")


class MockMetaAdsAdapter(MetaAdsAdapter):
    """In-memory Meta stand-in. Records calls; never opens network sockets."""

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.network_calls = 0
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"mock_{prefix}_{self._seq}"

    def create_campaign(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_campaign", kwargs))
        return {"id": self._next_id("campaign"), "status": "PAUSED"}

    def create_adset(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_adset", kwargs))
        return {"id": self._next_id("adset"), "status": "PAUSED"}

    def create_creative_from_existing_post(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_creative_from_existing_post", kwargs))
        return {"id": self._next_id("creative"), "status": "mock_created"}

    def create_ad(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_ad", kwargs))
        return {"id": self._next_id("ad"), "status": "PAUSED"}

    def get_insights(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_insights", kwargs))
        return {"data": []}

    def pause_ad(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("pause_ad", kwargs))
        return {"id": kwargs.get("ad_id"), "status": "PAUSED"}

    def resume_ad(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("resume_ad", kwargs))
        return {"id": kwargs.get("ad_id"), "status": "ACTIVE"}


HttpTransport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]


def _default_http_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes]:
    req = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — Meta Graph HTTPS only
            return int(resp.status), resp.read()
    except HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        return int(exc.code), raw or b"{}"
    except URLError as exc:
        raise classify_meta_error(http_status=None, network_message=str(exc.reason or exc)) from exc


class MetaMarketingApiAdapter(MetaAdsAdapter):
    """Real Meta Graph / Marketing API client (HTTP only; no browser/SDK).

    Safety:
    - GET allowed when token present
    - POST/PATCH/DELETE require META_WRITE_ENABLED=true
    - ACTIVE status rejected while META_ACTIVE_ENABLED=false
    - Creates force status=PAUSED
    - Budget caps re-validated before write
    - GET may retry transient 5xx; POST never auto-retries
    """

    def __init__(
        self,
        meta: MetaConfig | None = None,
        *,
        budget: BudgetConfig | None = None,
        transport: HttpTransport | None = None,
    ):
        self.meta = meta or load_meta_config()
        self.budget = budget or load_budget_config()
        self._transport = transport or _default_http_transport
        self.network_calls = 0
        self.request_log: list[dict[str, Any]] = []  # method/path only — no token

    # ── HTTP core ───────────────────────────────────────────────────────

    def _ensure_token(self) -> None:
        if not self.meta.token_set:
            raise MetaTokenMissing(
                "META_ACCESS_TOKEN is missing — set it in local .env (never commit)"
            )

    def _redact_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        if not params:
            return {}
        out: dict[str, Any] = {}
        for key, value in params.items():
            if key.lower() in {"access_token", "appsecret_proof", "authorization"}:
                out[key] = "***"
            else:
                out[key] = value
        return out

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        allow_retry: bool | None = None,
    ) -> dict[str, Any]:
        self._ensure_token()
        method_u = method.upper()
        if method_u in {"POST", "PATCH", "DELETE", "PUT"}:
            assert_write_enabled(self.meta)
            # Never blind-retry mutating calls (duplicate campaign risk).
            max_attempts = 1
        else:
            max_attempts = (
                1 + max(0, int(self.meta.get_max_retries))
                if (allow_retry is None or allow_retry)
                else 1
            )

        query = dict(params or {})
        query["access_token"] = self.meta.access_token
        path_clean = path if path.startswith("/") else f"/{path}"
        url = f"{self.meta.graph_root}{path_clean}"
        if query:
            # For POST with form body, token still goes as query param (Meta convention)
            # unless body already carries fields — we always pass token as query for GET,
            # and for POST as query too so body stays clean.
            url = f"{url}?{urlencode(query)}"

        body_bytes: bytes | None = None
        headers = {"Accept": "application/json", "User-Agent": "Agent10-MetaMarketingApi/1.0"}
        if data is not None:
            body_bytes = urlencode(
                {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in data.items()}
            ).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        safe_entry = {
            "method": method_u,
            "path": path_clean,
            "params": self._redact_params(params),
            "has_body": data is not None,
        }
        self.request_log.append(safe_entry)
        logger.debug("Meta API %s %s", method_u, path_clean)

        last_error: MetaApiError | None = None
        for attempt in range(max_attempts):
            self.network_calls += 1
            try:
                status, raw = self._transport(
                    method_u,
                    url,
                    headers,
                    body_bytes,
                    float(self.meta.request_timeout_sec),
                )
            except MetaApiError as exc:
                last_error = exc
                transient = isinstance(exc, (MetaNetworkError, MetaServerError))
                if transient and attempt + 1 < max_attempts:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise

            payload: Any
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {"error": {"message": raw.decode("utf-8", errors="replace")[:500]}}

            if status >= 500:
                err = classify_meta_error(http_status=status, payload=payload)
                last_error = err
                if attempt + 1 < max_attempts:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise err
            if status >= 400 or (isinstance(payload, dict) and "error" in payload):
                raise classify_meta_error(http_status=status, payload=payload)
            if not isinstance(payload, dict):
                return {"data": payload}
            return payload

        assert last_error is not None
        raise last_error

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def _paginate(
        self,
        path: str,
        *,
        params: dict[str, Any],
        limit: int | None = None,
        data_key: str = "data",
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        next_path = path
        next_params: dict[str, Any] | None = dict(params)
        while next_path:
            page = self._request("GET", next_path, params=next_params)
            rows = page.get(data_key) if isinstance(page, dict) else None
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        collected.append(row)
                        if limit is not None and len(collected) >= limit:
                            return collected[:limit]
            paging = page.get("paging") if isinstance(page, dict) else None
            cursors = paging.get("cursors") if isinstance(paging, dict) else None
            after = cursors.get("after") if isinstance(cursors, dict) else None
            # Prefer cursor continuation on same path; fall back to absolute next URL path.
            if after and (limit is None or len(collected) < limit):
                next_params = dict(params)
                next_params["after"] = after
                next_path = path
                continue
            next_url = paging.get("next") if isinstance(paging, dict) else None
            if next_url and (limit is None or len(collected) < limit):
                # Absolute next URL from Meta — extract path after version.
                # Avoid embedding token from next URL into logs; re-auth via our token.
                from urllib.parse import urlparse, parse_qs

                parsed = urlparse(str(next_url))
                # path like /v26.0/act_xxx/campaigns
                parts = [p for p in parsed.path.split("/") if p]
                if parts and parts[0].startswith("v"):
                    next_path = "/" + "/".join(parts[1:])
                else:
                    next_path = parsed.path
                q = {k: v[0] for k, v in parse_qs(parsed.query).items() if k != "access_token"}
                next_params = q
                continue
            break
        return collected

    # ── Read-only ───────────────────────────────────────────────────────

    def get_ad_account(self) -> dict[str, Any]:
        raw = self._get(f"/{self.meta.act_id}", fields=ACCOUNT_FIELDS)
        return normalize_account(raw)

    def list_campaigns(self, limit: int = 25) -> list[dict[str, Any]]:
        rows = self._paginate(
            f"/{self.meta.act_id}/campaigns",
            params={"fields": CAMPAIGN_FIELDS, "limit": min(int(limit), 100)},
            limit=limit,
        )
        return [normalize_campaign(r) for r in rows]

    def get_account_insights(
        self,
        *,
        date_preset: str = "last_7d",
        fields: str = INSIGHT_FIELDS,
    ) -> PaidPerformanceMetrics:
        raw = self._get(
            f"/{self.meta.act_id}/insights",
            fields=fields,
            date_preset=date_preset,
        )
        return normalize_insights(raw)

    def get_campaign_insights(
        self,
        campaign_id: str,
        *,
        date_preset: str = "last_7d",
        fields: str = INSIGHT_FIELDS,
    ) -> PaidPerformanceMetrics:
        raw = self._get(
            f"/{campaign_id}",
            fields=f"insights.date_preset({date_preset}){{{fields}}}",
        )
        insights = raw.get("insights") if isinstance(raw, dict) else None
        if insights is None:
            raw = self._get(
                f"/{campaign_id}/insights",
                fields=fields,
                date_preset=date_preset,
            )
            return normalize_insights(raw)
        return normalize_insights(insights if isinstance(insights, dict) else {"data": insights})

    def get_insights(self, **kwargs: Any) -> dict[str, Any]:
        """ABC-compatible insights fetch; returns raw+normalized."""
        campaign_id = kwargs.get("campaign_id")
        date_preset = str(kwargs.get("date_preset") or "last_7d")
        if campaign_id:
            metrics = self.get_campaign_insights(str(campaign_id), date_preset=date_preset)
        else:
            metrics = self.get_account_insights(date_preset=date_preset)
        return {"data": [metrics.to_dict()], "normalized": metrics.to_dict()}

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        raw = self._get(
            f"/{campaign_id}",
            fields="id,name,status,effective_status,objective",
        )
        return {
            "id": str(raw["id"]) if raw.get("id") is not None else None,
            "name": str(raw["name"]) if raw.get("name") is not None else None,
            "status": str(raw["status"]) if raw.get("status") is not None else None,
            "effective_status": (
                str(raw["effective_status"])
                if raw.get("effective_status") is not None
                else None
            ),
            "objective": str(raw["objective"]) if raw.get("objective") is not None else None,
        }

    def get_adsets(self, *, campaign_id: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        if campaign_id:
            path = f"/{campaign_id}/adsets"
        else:
            path = f"/{self.meta.act_id}/adsets"
        return self._paginate(
            path,
            params={"fields": ADSET_FIELDS, "limit": min(int(limit), 100)},
            limit=limit,
        )

    def get_ads(self, *, adset_id: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        if adset_id:
            path = f"/{adset_id}/ads"
        else:
            path = f"/{self.meta.act_id}/ads"
        return self._paginate(
            path,
            params={"fields": AD_FIELDS, "limit": min(int(limit), 100)},
            limit=limit,
        )

    # ── Writes (PAUSED only; write switch required) ─────────────────────

    def create_campaign(self, **kwargs: Any) -> dict[str, Any]:
        assert_write_enabled(self.meta)

        # Service-layer approval should gate business launches; raw adapter still safe.
        approval_state = kwargs.get("approval_state")
        if approval_state is not None:
            state = (
                approval_state
                if isinstance(approval_state, ApprovalState)
                else ApprovalState(str(approval_state))
            )
            if state not in {ApprovalState.APPROVED, ApprovalState.READY_TO_LAUNCH}:
                raise MetaApprovalRequired(
                    f"campaign create requires APPROVED/READY_TO_LAUNCH (got {state.value})"
                )

        name = str(kwargs.get("name") or "").strip()
        if not name:
            raise MetaSafetyError("create_campaign requires name")

        objective = assert_objective_allowed(
            str(kwargs.get("objective") or self.budget.default_objective),
            allowed=self.meta.allowed_objectives,
        )
        status = force_paused_status(kwargs.get("status"), meta=self.meta)

        daily_budget = kwargs.get("daily_budget")
        duration_days = kwargs.get("duration_days")
        if daily_budget is not None and duration_days is not None:
            assert_budget_within_caps(
                self.budget,
                daily_budget=float(daily_budget),
                duration_days=int(duration_days),
                total_budget=kwargs.get("total_budget") or kwargs.get("max_total_budget"),
            )

        categories = kwargs.get("special_ad_categories")
        if categories is None:
            categories = list(self.meta.special_ad_categories)
        if not isinstance(categories, list):
            categories = list(categories)

        payload: dict[str, Any] = {
            "name": name,
            "objective": objective,
            "status": status,
            "special_ad_categories": categories,
        }
        # Meta requires this when campaign budget is not used (no CBO).
        if daily_budget is not None and kwargs.get("set_campaign_budget"):
            payload["daily_budget"] = thb_to_meta_minor_units(daily_budget)
        else:
            sharing = kwargs.get("is_adset_budget_sharing_enabled")
            if sharing is None:
                sharing = False
            payload["is_adset_budget_sharing_enabled"] = bool(sharing)

        return self._request(
            "POST",
            f"/{self.meta.act_id}/campaigns",
            data=payload,
            allow_retry=False,
        )

    def create_adset(self, **kwargs: Any) -> dict[str, Any]:
        assert_write_enabled(self.meta)
        status = force_paused_status(kwargs.get("status"), meta=self.meta)
        campaign_id = kwargs.get("campaign_id")
        name = str(kwargs.get("name") or "").strip()
        if not campaign_id or not name:
            raise MetaSafetyError("create_adset requires campaign_id and name")

        daily_budget = kwargs.get("daily_budget")
        duration_days = int(kwargs.get("duration_days") or self.budget.default_duration_days)
        if daily_budget is not None:
            assert_budget_within_caps(
                self.budget,
                daily_budget=float(daily_budget),
                duration_days=duration_days,
                total_budget=kwargs.get("total_budget"),
            )

        billing_event = str(kwargs.get("billing_event") or "IMPRESSIONS")
        optimization_goal = str(kwargs.get("optimization_goal") or "POST_ENGAGEMENT")

        payload: dict[str, Any] = {
            "name": name,
            "campaign_id": str(campaign_id),
            "status": status,
            "billing_event": billing_event,
            "optimization_goal": optimization_goal,
            # Geo-only default; Advantage+ automatic placements = omit publisher_platforms.
            "targeting": kwargs.get("targeting")
            or {"geo_locations": {"countries": ["TH"]}},
        }
        if daily_budget is not None:
            payload["daily_budget"] = thb_to_meta_minor_units(daily_budget)

        # Optional Meta contract fields (pass-through when provided).
        for key in (
            "bid_strategy",
            "bid_amount",
            "destination_type",
            "promoted_object",
            "start_time",
            "end_time",
            "lifetime_budget",
            "pacing_type",
        ):
            if kwargs.get(key) is not None:
                payload[key] = kwargs[key]

        return self._request(
            "POST",
            f"/{self.meta.act_id}/adsets",
            data=payload,
            allow_retry=False,
        )

    def get_adset(self, adset_id: str) -> dict[str, Any]:
        raw = self._get(
            f"/{adset_id}",
            fields=(
                "id,name,campaign_id,status,effective_status,"
                "daily_budget,lifetime_budget,optimization_goal,billing_event,"
                "targeting,start_time,end_time,destination_type,promoted_object"
            ),
        )
        return {
            "id": str(raw["id"]) if raw.get("id") is not None else None,
            "name": str(raw["name"]) if raw.get("name") is not None else None,
            "campaign_id": (
                str(raw["campaign_id"]) if raw.get("campaign_id") is not None else None
            ),
            "status": str(raw["status"]) if raw.get("status") is not None else None,
            "effective_status": (
                str(raw["effective_status"])
                if raw.get("effective_status") is not None
                else None
            ),
            "daily_budget": raw.get("daily_budget"),
            "lifetime_budget": raw.get("lifetime_budget"),
            "optimization_goal": raw.get("optimization_goal"),
            "billing_event": raw.get("billing_event"),
            "targeting": raw.get("targeting"),
            "start_time": raw.get("start_time"),
            "end_time": raw.get("end_time"),
            "destination_type": raw.get("destination_type"),
            "promoted_object": raw.get("promoted_object"),
        }

    def create_creative_from_existing_post(self, **kwargs: Any) -> dict[str, Any]:
        """Legacy ABC entry — routes to Facebook existing-post creative when object_story_id set."""
        if kwargs.get("object_story_id") or kwargs.get("facebook_post_id"):
            return self.create_creative_from_existing_facebook_post(**kwargs)
        if kwargs.get("instagram_media_id"):
            return self.create_creative_from_existing_instagram_media(**kwargs)
        raise MetaSafetyError(
            "create_creative_from_existing_post requires facebook_post_id/object_story_id "
            "or instagram_media_id"
        )

    def create_creative_from_existing_facebook_post(self, **kwargs: Any) -> dict[str, Any]:
        """Create ad creative from an existing Facebook Page post (object_story_id).

        Meta contract: AdCreative with object_story_id confirmed via Graph (typically
        "{page_id}_{post_id}" as returned by Page published_posts.id — never invent).
        """
        assert_write_enabled(self.meta)
        story_id = kwargs.get("object_story_id") or kwargs.get("facebook_post_id")
        name = str(kwargs.get("name") or "fb_existing_post").strip()
        story_id = validate_object_story_id(story_id)
        payload = {
            "name": name,
            "object_story_id": story_id,
        }
        return self._request(
            "POST",
            f"/{self.meta.act_id}/adcreatives",
            data=payload,
            allow_retry=False,
        )

    def get_adcreative(self, creative_id: str) -> dict[str, Any]:
        """GET creative; drops unsupported fields cleanly if Meta rejects the field set."""
        fields_primary = (
            "id,name,object_story_id,status,effective_object_story_id,thumbnail_url"
        )
        fields_fallback = "id,name,object_story_id,status,effective_object_story_id"
        try:
            raw = self._get(f"/{creative_id}", fields=fields_primary)
        except MetaUnsupportedField:
            raw = self._get(f"/{creative_id}", fields=fields_fallback)
        except MetaApiError as exc:
            # Unsupported field may be classified generically on some payloads.
            if "nonexisting field" in str(exc).lower() or "unknown field" in str(exc).lower():
                raw = self._get(f"/{creative_id}", fields=fields_fallback)
            else:
                raise
        return {
            "id": str(raw["id"]) if raw.get("id") is not None else None,
            "name": str(raw["name"]) if raw.get("name") is not None else None,
            "object_story_id": (
                str(raw["object_story_id"]) if raw.get("object_story_id") is not None else None
            ),
            "status": str(raw["status"]) if raw.get("status") is not None else None,
            "effective_object_story_id": (
                str(raw["effective_object_story_id"])
                if raw.get("effective_object_story_id") is not None
                else None
            ),
            "thumbnail_url": raw.get("thumbnail_url"),
        }

    def list_adcreatives(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._paginate(
            f"/{self.meta.act_id}/adcreatives",
            params={
                "fields": "id,name,object_story_id,status,effective_object_story_id",
                "limit": min(int(limit), 100),
            },
            limit=limit,
        )

    def get_page(self) -> dict[str, Any]:
        """Read-only Page info (uses Marketing/user token)."""
        raw = self._get(f"/{self.meta.page_id}", fields="id,name,link")
        return {
            "id": str(raw["id"]) if raw.get("id") is not None else None,
            "name": str(raw["name"]) if raw.get("name") is not None else None,
            "link": raw.get("link"),
        }

    def get_page_access_token(self) -> str:
        """Fetch Page access token for Page feed reads. Never log the value."""
        raw = self._get(f"/{self.meta.page_id}", fields="id,access_token")
        token = str(raw.get("access_token") or "").strip()
        if not token:
            raise MetaPermissionDenied(
                "Page access token unavailable — check pages_read_engagement / Page role"
            )
        return token

    def list_facebook_page_posts(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Read-only Page published_posts via Page access token."""
        page_token = self.get_page_access_token()
        # Temporary token override for Page edge only — restore after.
        original = self.meta.access_token
        try:
            # MetaConfig is frozen — use a shallow override on a copy for the request.
            from dataclasses import replace

            self.meta = replace(self.meta, access_token=page_token)
            rows = self._paginate(
                f"/{self.meta.page_id}/published_posts",
                params={
                    "fields": "id,created_time,message,permalink_url,status_type,is_published",
                    "limit": min(int(limit), 50),
                },
                limit=limit,
            )
        finally:
            self.meta = replace(self.meta, access_token=original)
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": str(row["id"]) if row.get("id") is not None else None,
                    "created_time": row.get("created_time"),
                    "message": row.get("message"),
                    "permalink_url": row.get("permalink_url"),
                    "status_type": row.get("status_type"),
                    "is_published": row.get("is_published"),
                }
            )
        return out

    def confirm_facebook_object_story_id(self, candidate: str) -> str:
        """Confirm object_story_id via Meta GET (Page token). Returns official id."""
        story_id = validate_object_story_id(candidate)
        page_token = self.get_page_access_token()
        from dataclasses import replace

        original = self.meta.access_token
        try:
            self.meta = replace(self.meta, access_token=page_token)
            raw = self._get(
                f"/{story_id}",
                fields="id,created_time,message,permalink_url,is_published",
            )
        finally:
            self.meta = replace(self.meta, access_token=original)
        confirmed = str(raw.get("id") or "").strip()
        if not confirmed:
            raise MetaSafetyError(f"Meta GET did not return id for {story_id}")
        if confirmed != story_id:
            # Prefer Meta's returned id as canonical object_story_id.
            return validate_object_story_id(confirmed)
        return confirmed

    def create_creative_from_existing_instagram_media(self, **kwargs: Any) -> dict[str, Any]:
        """Stub: Instagram existing-media → AdCreative.

        TODO: Confirm current official Meta Marketing API contract for promoting an
        existing Instagram media/post as an ad creative (field names vary by API
        version: source_instagram_media_id / instagram_user_id / object_id).
        Do not guess live POST shapes. Official docs required before enabling.
        """
        assert_write_enabled(self.meta)
        media_id = kwargs.get("instagram_media_id")
        if not media_id:
            raise MetaSafetyError("instagram_media_id required")
        raise MetaSafetyError(
            "TODO: create_creative_from_existing_instagram_media requires confirmed "
            "official Meta Marketing API contract for this Graph version — live POST blocked"
        )

    def create_ad(self, **kwargs: Any) -> dict[str, Any]:
        assert_write_enabled(self.meta)
        status = force_paused_status(kwargs.get("status"), meta=self.meta)
        name = str(kwargs.get("name") or "").strip()
        adset_id = kwargs.get("adset_id")
        creative_id = kwargs.get("creative_id")
        if not name or not adset_id or not creative_id:
            raise MetaSafetyError("create_ad requires name, adset_id, creative_id")
        payload = {
            "name": name,
            "adset_id": str(adset_id),
            "creative": {"creative_id": str(creative_id)},
            "status": status,
        }
        return self._request(
            "POST",
            f"/{self.meta.act_id}/ads",
            data=payload,
            allow_retry=False,
        )

    def pause_campaign(self, *, campaign_id: str, **kwargs: Any) -> dict[str, Any]:
        assert_write_enabled(self.meta)
        return self._request(
            "POST",
            f"/{campaign_id}",
            data={"status": FORCED_CREATE_STATUS},
            allow_retry=False,
        )

    def pause_adset(self, *, adset_id: str, **kwargs: Any) -> dict[str, Any]:
        assert_write_enabled(self.meta)
        return self._request(
            "POST",
            f"/{adset_id}",
            data={"status": FORCED_CREATE_STATUS},
            allow_retry=False,
        )

    def pause_ad(self, **kwargs: Any) -> dict[str, Any]:
        assert_write_enabled(self.meta)
        ad_id = kwargs.get("ad_id")
        if not ad_id:
            raise MetaSafetyError("pause_ad requires ad_id")
        return self._request(
            "POST",
            f"/{ad_id}",
            data={"status": FORCED_CREATE_STATUS},
            allow_retry=False,
        )

    def resume_ad(self, **kwargs: Any) -> dict[str, Any]:
        """Resume → ACTIVE is blocked unless META_ACTIVE_ENABLED (still not production-ready)."""
        assert_write_enabled(self.meta)
        if not self.meta.active_enabled:
            raise MetaActiveDisabled(
                "ACTIVE launch disabled in current safety mode "
                "(META_ACTIVE_ENABLED=false) — resume_ad blocked"
            )
        raise MetaActiveDisabled(
            "META_ACTIVE_ENABLED must not be used for automatic production launch yet"
        )

    def change_budget(self, **kwargs: Any) -> dict[str, Any]:
        """Interface-only budget change — no real network call in current stage."""
        assert_write_enabled(self.meta)
        raise MetaSafetyError(
            "change_budget is interface-only — real budget mutations disabled; "
            f"max step pct config={self.budget.max_budget_change_pct}"
        )
