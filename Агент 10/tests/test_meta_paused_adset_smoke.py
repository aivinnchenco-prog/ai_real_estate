"""Offline tests for Meta PAUSED ad set smoke-test."""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from agent10_marketer.adapters.meta_ads import MetaMarketingApiAdapter
from agent10_marketer.adapters.meta_errors import (
    MetaActiveDisabled,
    MetaBudgetPolicyError,
    MetaSafetyError,
    MetaWriteDisabled,
)
from agent10_marketer.adapters.meta_policy import force_paused_status
from agent10_marketer.config import BudgetConfig, MetaConfig
from agent10_marketer.meta_smoke import (
    SMOKE_ADSET_NAME,
    SMOKE_CAMPAIGN_ID,
    SMOKE_TARGETING,
    create_paused_smoke_adset,
    find_adsets_by_exact_name,
    run_adset_smoke_preflight,
)

SECRET = "ADSET_SMOKE_TOKEN_DO_NOT_LOG"


def _meta(**overrides: Any) -> MetaConfig:
    base = dict(
        app_id="1051487844031310",
        business_id="1572755037543832",
        ad_account_id="3462495317264561",
        page_id="1189108177625326",
        instagram_account_id="17841410402639080",
        access_token=SECRET,
        write_enabled=True,
        active_enabled=False,
        ads_enabled=False,
        expected_account_name="Open Home th",
        expected_currency="THB",
        expected_timezone="Asia/Bangkok",
        get_max_retries=2,
    )
    base.update(overrides)
    return MetaConfig(**base)


def _budget(**overrides: Any) -> BudgetConfig:
    base = dict(
        max_daily_budget=1500,
        max_duration_days=14,
        max_total_budget=2500,
    )
    base.update(overrides)
    return BudgetConfig(**base)


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method, url, headers, body, timeout):
        parsed = urlparse(url)
        self.calls.append({"method": method, "path": parsed.path, "body": body})
        return self.handler(method, url, headers, body, timeout)


def _jb(obj: Any) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _account_ok():
    return {
        "id": "act_3462495317264561",
        "name": "Open Home th",
        "account_status": 1,
        "currency": "THB",
        "timezone_name": "Asia/Bangkok",
    }


def _campaign_ok():
    return {
        "id": SMOKE_CAMPAIGN_ID,
        "name": "OPENHOME_AGENT10_SMOKE_TEST_2026_08_09",
        "status": "PAUSED",
        "effective_status": "PAUSED",
        "objective": "OUTCOME_ENGAGEMENT",
        "special_ad_categories": [],
        "special_ad_category": "NONE",
    }


def test_adset_smoke_requires_confirm(capsys):
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "meta_create_paused_adset_smoke.py"
    )
    spec = importlib.util.spec_from_file_location("meta_create_paused_adset_smoke", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    code = mod.main([])
    out = capsys.readouterr().out
    assert code == 2
    assert "LIVE WRITE DISABLED WITHOUT --confirm" in out


def test_active_rejected():
    with pytest.raises(MetaActiveDisabled):
        force_paused_status("ACTIVE", meta=_meta(active_enabled=False))


def test_write_false_blocks_adset_post():
    def handler(*a, **k):
        raise AssertionError("network blocked")

    adapter = MetaMarketingApiAdapter(
        _meta(write_enabled=False), budget=_budget(), transport=FakeTransport(handler)
    )
    with pytest.raises(MetaWriteDisabled):
        create_paused_smoke_adset(adapter, _meta(write_enabled=False))


def test_wrong_campaign_blocked():
    def handler(*a, **k):
        raise AssertionError("must not POST")

    adapter = MetaMarketingApiAdapter(
        _meta(), budget=_budget(), transport=FakeTransport(handler)
    )
    with pytest.raises(MetaSafetyError, match="wrong campaign"):
        create_paused_smoke_adset(adapter, _meta(), campaign_id="999")


def test_budget_cap_enforced_before_adset_post():
    def handler(*a, **k):
        raise AssertionError("budget over cap")

    adapter = MetaMarketingApiAdapter(
        _meta(), budget=_budget(), transport=FakeTransport(handler)
    )
    with pytest.raises(MetaBudgetPolicyError):
        create_paused_smoke_adset(adapter, _meta(), daily_budget=9999)


def test_duplicate_adset_name_detected_without_create():
    state = {"posts": 0}

    def handler(method, url, headers, body, timeout):
        if method == "GET" and "/adsets" in url:
            return 200, _jb(
                {
                    "data": [
                        {
                            "id": "55",
                            "name": SMOKE_ADSET_NAME,
                            "status": "PAUSED",
                            "campaign_id": SMOKE_CAMPAIGN_ID,
                        }
                    ]
                }
            )
        if method == "POST":
            state["posts"] += 1
            return 200, _jb({"id": "x"})
        raise AssertionError(url)

    adapter = MetaMarketingApiAdapter(
        _meta(), budget=_budget(), transport=FakeTransport(handler)
    )
    found = find_adsets_by_exact_name(adapter, SMOKE_ADSET_NAME)
    assert len(found) == 1
    assert state["posts"] == 0


def test_housing_status_none_on_smoke_campaign():
    def handler(method, url, headers, body, timeout):
        if url.rstrip("/").endswith(SMOKE_CAMPAIGN_ID) or f"/{SMOKE_CAMPAIGN_ID}?" in url or f"/{SMOKE_CAMPAIGN_ID}" in urlparse(url).path:
            return 200, _jb(_campaign_ok())
        return 200, _jb(_account_ok())

    adapter = MetaMarketingApiAdapter(
        _meta(), budget=_budget(), transport=FakeTransport(handler)
    )
    pre = run_adset_smoke_preflight(adapter, _meta())
    assert pre.ok
    assert "NONE" in pre.housing_status
    # Housing-sensitive fields must stay absent; age_min=20 is required for TH youth rules.
    blob = json.dumps(SMOKE_TARGETING)
    assert "genders" not in blob
    assert "flexible_spec" not in blob
    assert "interests" not in blob
    assert SMOKE_TARGETING.get("age_min") == 20

def test_adset_create_payload_contract(caplog):
    def handler(method, url, headers, body, timeout):
        assert method == "POST"
        form = parse_qs(body.decode("utf-8"))
        assert form["status"] == ["PAUSED"]
        assert form["campaign_id"] == [SMOKE_CAMPAIGN_ID]
        assert form["optimization_goal"] == ["POST_ENGAGEMENT"]
        assert form["billing_event"] == ["IMPRESSIONS"]
        assert form["destination_type"] == ["ON_POST"]
        assert form["bid_strategy"] == ["LOWEST_COST_WITHOUT_CAP"]
        assert form["daily_budget"] == ["10000"]  # 100 THB → minor units
        targeting = json.loads(form["targeting"][0])
        assert targeting == {"geo_locations": {"countries": ["TH"]}, "age_min": 20}
        promoted = json.loads(form["promoted_object"][0])
        assert promoted == {"page_id": "1189108177625326"}
        assert "ACTIVE" not in body.decode("utf-8")
        return 200, _jb({"id": "adset_1"})

    transport = FakeTransport(handler)
    adapter = MetaMarketingApiAdapter(_meta(), budget=_budget(), transport=transport)
    with caplog.at_level(logging.DEBUG):
        result = create_paused_smoke_adset(adapter, _meta())
    assert result["id"] == "adset_1"
    assert SECRET not in " ".join(r.getMessage() for r in caplog.records)
    assert SECRET not in json.dumps(adapter.request_log)
    assert len([c for c in transport.calls if c["method"] == "POST"]) == 1


def test_post_no_blind_retry_adset():
    state = {"n": 0}

    def handler(method, url, headers, body, timeout):
        state["n"] += 1
        return 500, _jb({"error": {"message": "temporary"}})

    adapter = MetaMarketingApiAdapter(
        _meta(get_max_retries=5), budget=_budget(), transport=FakeTransport(handler)
    )
    with pytest.raises(Exception):
        create_paused_smoke_adset(adapter, _meta())
    assert state["n"] == 1



def test_no_creative_ad_in_adset_smoke_script():
    """Ad set smoke script must not create creative/ad (helpers may exist separately)."""
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "meta_create_paused_adset_smoke.py"
    )
    script_text = script.read_text(encoding="utf-8")
    assert "create_paused_smoke_adset" in script_text
    assert "create_ad(" not in script_text
    assert "create_creative_from_existing" not in script_text
    assert "create_paused_smoke_fb_creative" not in script_text

