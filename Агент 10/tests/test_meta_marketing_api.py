"""Offline Meta Marketing API adapter tests — no live network / no real token."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from agent10_marketer.adapters.meta_ads import (
    DisabledMetaAdsAdapter,
    MetaIntegrationDisabled,
    MetaMarketingApiAdapter,
    MockMetaAdsAdapter,
)
from agent10_marketer.adapters.meta_errors import (
    MetaActiveDisabled,
    MetaBudgetPolicyError,
    MetaPermissionDenied,
    MetaRateLimited,
    MetaTokenExpired,
    MetaTokenMissing,
    MetaWriteDisabled,
    classify_meta_error,
)
from agent10_marketer.adapters.meta_insights import (
    normalize_account,
    normalize_insights,
    parse_meta_money,
    parse_meta_number,
)
from agent10_marketer.adapters.meta_policy import assert_budget_within_caps, force_paused_status
from agent10_marketer.approval import approve, advance_to_ready
from agent10_marketer.config import (
    DEFAULT_META_AD_ACCOUNT_ID,
    BudgetConfig,
    MetaConfig,
    load_config,
    load_meta_config,
)
from agent10_marketer.marketing_brain import DisabledMarketingBrain
from agent10_marketer.models import ApprovalRecord, ApprovalState
from agent10_marketer.service import PerformanceMarketerService


SECRET_TOKEN = "TEST_TOKEN_DO_NOT_LOG_OR_COMMIT_abc123"


def _meta(**overrides: Any) -> MetaConfig:
    base = dict(
        app_id="1051487844031310",
        business_id="1572755037543832",
        ad_account_id=DEFAULT_META_AD_ACCOUNT_ID,
        page_id="1189108177625326",
        instagram_account_id="17841410402639080",
        access_token=SECRET_TOKEN,
        graph_api_version="v26.0",
        api_base_url="https://graph.facebook.com",
        write_enabled=False,
        active_enabled=False,
        ads_enabled=True,
        expected_account_name="Open Home th",
        expected_currency="THB",
        expected_timezone="Asia/Bangkok",
        diagnostic_strict=False,
        get_max_retries=2,
        request_timeout_sec=5.0,
    )
    base.update(overrides)
    return MetaConfig(**base)


def _budget(**overrides: Any) -> BudgetConfig:
    base = dict(
        currency="THB",
        default_daily_budget=500,
        max_daily_budget=1500,
        default_duration_days=5,
        max_duration_days=14,
        max_total_budget=2500,
    )
    base.update(overrides)
    return BudgetConfig(**base)


class FakeTransport:
    """Deterministic HTTP stand-in. Never opens sockets."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, bytes]:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        # Strip token from recorded call for assertions
        self.calls.append(
            {
                "method": method,
                "path": parsed.path,
                "query_keys": sorted(qs.keys()),
                "has_access_token": "access_token" in qs,
                "headers": {k: v for k, v in headers.items() if k.lower() != "authorization"},
                "body": body,
                "timeout": timeout,
            }
        )
        return self.handler(method, url, headers, body, timeout)


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj).encode("utf-8")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_loads_meta_ids(monkeypatch):
    monkeypatch.setenv("META_APP_ID", "1051487844031310")
    monkeypatch.setenv("META_BUSINESS_ID", "1572755037543832")
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "3462495317264561")
    monkeypatch.setenv("META_PAGE_ID", "1189108177625326")
    monkeypatch.setenv("META_INSTAGRAM_ACCOUNT_ID", "17841410402639080")
    monkeypatch.setenv("META_GRAPH_API_VERSION", "v26.0")
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    meta = load_meta_config()
    assert meta.app_id == "1051487844031310"
    assert meta.business_id == "1572755037543832"
    assert meta.ad_account_id == "3462495317264561"
    assert meta.page_id == "1189108177625326"
    assert meta.instagram_account_id == "17841410402639080"
    assert meta.graph_api_version == "v26.0"
    assert meta.write_enabled is False
    assert meta.active_enabled is False
    assert meta.token_set is False
    assert "access_token" not in meta.redacted_dict()
    assert meta.redacted_dict()["token"] == "MISSING"


def test_token_missing_clear_failure():
    adapter = MetaMarketingApiAdapter(_meta(access_token=""), budget=_budget())
    with pytest.raises(MetaTokenMissing, match="META_ACCESS_TOKEN"):
        adapter.get_ad_account()


def test_token_never_logged(caplog):
    def handler(method, url, headers, body, timeout):
        assert SECRET_TOKEN in url  # transport sees it
        return 200, _json_bytes(
            {
                "id": "act_3462495317264561",
                "name": "Open Home th",
                "account_status": 1,
                "currency": "THB",
                "timezone_name": "Asia/Bangkok",
            }
        )

    transport = FakeTransport(handler)
    adapter = MetaMarketingApiAdapter(_meta(), budget=_budget(), transport=transport)
    with caplog.at_level(logging.DEBUG):
        adapter.get_ad_account()
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert SECRET_TOKEN not in joined
    assert SECRET_TOKEN not in json.dumps(adapter.request_log)
    assert all(c.get("params", {}).get("access_token") != SECRET_TOKEN for c in [adapter.request_log[0]])


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_get_account_normalized():
    raw = {
        "id": "act_3462495317264561",
        "name": "Open Home th",
        "account_status": 1,
        "currency": "THB",
        "timezone_name": "Asia/Bangkok",
    }
    acc = normalize_account(raw)
    assert acc["currency"] == "THB"
    assert acc["timezone_name"] == "Asia/Bangkok"
    assert acc["name"] == "Open Home th"


def test_insights_normalized_missing_vs_zero():
    metrics = normalize_insights(
        {
            "data": [
                {
                    "spend": "0",
                    "impressions": "10",
                    "reach": "5",
                    "clicks": "0",
                    "ctr": "0",
                    # cpc / cpm absent
                }
            ]
        }
    )
    assert metrics.spend == 0.0
    assert metrics.impressions == 10.0
    assert metrics.clicks == 0.0
    assert metrics.cpc is None
    assert metrics.cpm is None
    assert metrics.leads is None
    assert parse_meta_number(None) is None
    assert parse_meta_number("0") == 0.0
    assert parse_meta_money("12.50") is not None


def test_thb_bangkok_expected_ok():
    def handler(method, url, headers, body, timeout):
        if "/insights" in url:
            return 200, _json_bytes({"data": [{"spend": "1.00", "impressions": "100"}]})
        if "/campaigns" in url:
            return 200, _json_bytes({"data": [{"id": "1", "name": "c", "status": "PAUSED"}]})
        return 200, _json_bytes(
            {
                "id": "act_3462495317264561",
                "name": "Open Home th",
                "account_status": 1,
                "currency": "THB",
                "timezone_name": "Asia/Bangkok",
            }
        )

    adapter = MetaMarketingApiAdapter(
        _meta(), budget=_budget(), transport=FakeTransport(handler)
    )
    acc = adapter.get_ad_account()
    assert acc["currency"] == "THB"
    assert acc["timezone_name"] == "Asia/Bangkok"


def test_wrong_currency_and_timezone_detectable():
    acc = normalize_account(
        {
            "id": "x",
            "name": "Other",
            "currency": "USD",
            "timezone_name": "America/New_York",
            "account_status": 1,
        }
    )
    meta = _meta(diagnostic_strict=True)
    assert acc["currency"] != meta.expected_currency
    assert acc["timezone_name"] != meta.expected_timezone


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_campaigns_pagination():
    pages = {
        0: {
            "data": [{"id": "1", "name": "a", "status": "PAUSED", "effective_status": "PAUSED"}],
            "paging": {"cursors": {"after": "CURSOR1"}},
        },
        1: {
            "data": [{"id": "2", "name": "b", "status": "PAUSED", "effective_status": "PAUSED"}],
            "paging": {},
        },
    }
    state = {"n": 0}

    def handler(method, url, headers, body, timeout):
        assert method == "GET"
        idx = state["n"]
        state["n"] += 1
        return 200, _json_bytes(pages[idx])

    adapter = MetaMarketingApiAdapter(
        _meta(), budget=_budget(), transport=FakeTransport(handler)
    )
    camps = adapter.list_campaigns(limit=10)
    assert [c["id"] for c in camps] == ["1", "2"]
    assert state["n"] == 2


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_401_invalid_token_handled():
    err = classify_meta_error(
        http_status=401,
        payload={"error": {"message": "Invalid OAuth access token.", "type": "OAuthException", "code": 190}},
    )
    assert isinstance(err, MetaTokenExpired)


def test_permission_denied_handled():
    err = classify_meta_error(
        http_status=403,
        payload={
            "error": {
                "message": "(#200) Requires ads_read permission",
                "type": "OAuthException",
                "code": 200,
            }
        },
    )
    assert isinstance(err, MetaPermissionDenied)
    assert "ads_read" in str(err)


def test_rate_limit_handled():
    err = classify_meta_error(
        http_status=429,
        payload={"error": {"message": "User request limit reached", "code": 17}},
    )
    assert isinstance(err, MetaRateLimited)


def test_get_retry_only_transient():
    state = {"n": 0}

    def handler(method, url, headers, body, timeout):
        state["n"] += 1
        if state["n"] < 3:
            return 500, _json_bytes({"error": {"message": "temporary"}})
        return 200, _json_bytes(
            {
                "id": "act_1",
                "name": "Open Home th",
                "account_status": 1,
                "currency": "THB",
                "timezone_name": "Asia/Bangkok",
            }
        )

    adapter = MetaMarketingApiAdapter(
        _meta(get_max_retries=2), budget=_budget(), transport=FakeTransport(handler)
    )
    acc = adapter.get_ad_account()
    assert acc["currency"] == "THB"
    assert state["n"] == 3


def test_post_no_automatic_retry():
    state = {"n": 0}

    def handler(method, url, headers, body, timeout):
        state["n"] += 1
        return 500, _json_bytes({"error": {"message": "temporary"}})

    adapter = MetaMarketingApiAdapter(
        _meta(write_enabled=True, get_max_retries=5),
        budget=_budget(),
        transport=FakeTransport(handler),
    )
    with pytest.raises(Exception):
        adapter.create_campaign(
            name="test",
            objective="OUTCOME_ENGAGEMENT",
            status="PAUSED",
            daily_budget=100,
            duration_days=3,
        )
    assert state["n"] == 1


# ---------------------------------------------------------------------------
# Write safety
# ---------------------------------------------------------------------------


def test_write_disabled_blocks_create():
    def handler(method, url, headers, body, timeout):
        raise AssertionError("network must not be called")

    adapter = MetaMarketingApiAdapter(
        _meta(write_enabled=False), budget=_budget(), transport=FakeTransport(handler)
    )
    with pytest.raises(MetaWriteDisabled):
        adapter.create_campaign(name="x", objective="OUTCOME_ENGAGEMENT", status="PAUSED")


def test_paused_create_allowed_when_write_enabled():
    def handler(method, url, headers, body, timeout):
        assert method == "POST"
        assert body is not None
        form = parse_qs(body.decode("utf-8"))
        assert form["status"] == ["PAUSED"]
        assert form["objective"] == ["OUTCOME_ENGAGEMENT"]
        return 200, _json_bytes({"id": "camp_1"})

    adapter = MetaMarketingApiAdapter(
        _meta(write_enabled=True), budget=_budget(), transport=FakeTransport(handler)
    )
    result = adapter.create_campaign(
        name="paused_test",
        objective="OUTCOME_ENGAGEMENT",
        status="PAUSED",
        daily_budget=500,
        duration_days=5,
    )
    assert result["id"] == "camp_1"


def test_active_rejected():
    meta = _meta(write_enabled=True, active_enabled=False)
    with pytest.raises(MetaActiveDisabled, match="ACTIVE launch disabled"):
        force_paused_status("ACTIVE", meta=meta)

    def handler(method, url, headers, body, timeout):
        raise AssertionError("must not POST ACTIVE")

    adapter = MetaMarketingApiAdapter(meta, budget=_budget(), transport=FakeTransport(handler))
    with pytest.raises(MetaActiveDisabled):
        adapter.create_campaign(
            name="x",
            objective="OUTCOME_ENGAGEMENT",
            status="ACTIVE",
            daily_budget=100,
            duration_days=2,
        )


def test_budget_caps_enforced_before_post():
    def handler(method, url, headers, body, timeout):
        raise AssertionError("budget over cap must not reach network")

    adapter = MetaMarketingApiAdapter(
        _meta(write_enabled=True), budget=_budget(), transport=FakeTransport(handler)
    )
    with pytest.raises(MetaBudgetPolicyError, match="daily_budget"):
        adapter.create_campaign(
            name="x",
            objective="OUTCOME_ENGAGEMENT",
            status="PAUSED",
            daily_budget=9999,
            duration_days=5,
        )
    with pytest.raises(MetaBudgetPolicyError, match="duration"):
        adapter.create_campaign(
            name="x",
            objective="OUTCOME_ENGAGEMENT",
            status="PAUSED",
            daily_budget=100,
            duration_days=99,
        )
    with pytest.raises(MetaBudgetPolicyError, match="total_budget"):
        assert_budget_within_caps(
            _budget(), daily_budget=1500, duration_days=14, total_budget=99999
        )


def test_approval_required_at_service_layer(config, tmp_data_dir):
    from dataclasses import replace

    from agent10_marketer.approval import ApprovalError

    cfg = replace(config, meta=replace(config.meta, write_enabled=True, ads_enabled=True))
    mock = MockMetaAdsAdapter()
    service = PerformanceMarketerService(cfg, meta=mock)
    record = ApprovalRecord(
        approval_id="appr1",
        object_id="1847",
        publication_id="pub1",
        organic_score=80,
        daily_budget=500,
        duration_days=5,
        max_total_budget=2500,
        state=ApprovalState.PROPOSED,
        created_at=datetime.now(timezone.utc),
    )
    service.store.save_approval(record)

    with pytest.raises(ApprovalError):
        service.create_paused_campaign_from_approval("appr1")

    approve(record, approved_by="tester")
    advance_to_ready(record)
    service.store.save_approval(record)
    result = service.create_paused_campaign_from_approval("appr1")
    assert result["status"] == "PAUSED"
    assert mock.calls[0][0] == "create_campaign"


def test_diagnostic_sends_only_get():
    def handler(method, url, headers, body, timeout):
        assert method == "GET"
        if url.rstrip("/").endswith("insights") or "/insights?" in url or "/insights" in url:
            return 200, _json_bytes({"data": [{"spend": "0", "impressions": "0"}]})
        if "/campaigns" in url:
            return 200, _json_bytes({"data": []})
        return 200, _json_bytes(
            {
                "id": f"act_{DEFAULT_META_AD_ACCOUNT_ID}",
                "name": "Open Home th",
                "account_status": 1,
                "currency": "THB",
                "timezone_name": "Asia/Bangkok",
            }
        )

    transport = FakeTransport(handler)
    adapter = MetaMarketingApiAdapter(_meta(), budget=_budget(), transport=transport)
    adapter.get_ad_account()
    adapter.list_campaigns(limit=5)
    adapter.get_account_insights()
    assert all(c["method"] == "GET" for c in transport.calls)
    assert all(e["method"] == "GET" for e in adapter.request_log)


def test_mock_and_disabled_still_work():
    mock = MockMetaAdsAdapter()
    assert mock.create_campaign(name="x")["status"] == "PAUSED"
    assert mock.network_calls == 0
    disabled = DisabledMetaAdsAdapter()
    with pytest.raises(MetaIntegrationDisabled):
        disabled.get_insights()


def test_marketing_brain_disabled_no_meta_access():
    brain = DisabledMarketingBrain()
    out = brain.analyze_campaign(metrics=normalize_insights({"data": [{"spend": "1"}]}))
    assert out["status"] == "disabled"


def test_instagram_creative_stub_blocked():
    def handler(method, url, headers, body, timeout):
        raise AssertionError("instagram creative stub must not POST")

    adapter = MetaMarketingApiAdapter(
        _meta(write_enabled=True), budget=_budget(), transport=FakeTransport(handler)
    )
    from agent10_marketer.adapters.meta_errors import MetaSafetyError

    with pytest.raises(MetaSafetyError, match="TODO"):
        adapter.create_creative_from_existing_instagram_media(instagram_media_id="1784")


def test_load_config_includes_meta(tmp_data_dir, monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(config_dir=root / "config", data_dir=tmp_data_dir)
    assert cfg.meta.ad_account_id == DEFAULT_META_AD_ACCOUNT_ID
    assert cfg.meta.write_enabled is False
