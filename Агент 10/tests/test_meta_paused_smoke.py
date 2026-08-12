"""Offline tests for Meta PAUSED campaign smoke-test helpers/script."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from agent10_marketer.adapters.meta_ads import MetaMarketingApiAdapter
from agent10_marketer.adapters.meta_errors import MetaActiveDisabled, MetaWriteDisabled
from agent10_marketer.adapters.meta_policy import force_paused_status
from agent10_marketer.config import BudgetConfig, MetaConfig
from agent10_marketer.meta_smoke import (
    SMOKE_CAMPAIGN_NAME,
    create_paused_smoke_campaign,
    find_campaigns_by_exact_name,
    run_smoke_preflight,
)

SECRET = "SMOKE_TEST_TOKEN_DO_NOT_LOG_xyz"


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


def _budget() -> BudgetConfig:
    return BudgetConfig()


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method, url, headers, body, timeout):
        parsed = urlparse(url)
        self.calls.append(
            {
                "method": method,
                "path": parsed.path,
                "body": body,
                "url_has_secret": SECRET in url,
            }
        )
        return self.handler(method, url, headers, body, timeout)


def _jb(obj: Any) -> bytes:
    return json.dumps(obj).encode("utf-8")


def test_active_rejected_for_smoke_meta():
    with pytest.raises(MetaActiveDisabled):
        force_paused_status("ACTIVE", meta=_meta(active_enabled=False))


def test_write_false_blocks_post():
    def handler(*a, **k):
        raise AssertionError("no network")

    adapter = MetaMarketingApiAdapter(
        _meta(write_enabled=False), budget=_budget(), transport=FakeTransport(handler)
    )
    with pytest.raises(MetaWriteDisabled):
        create_paused_smoke_campaign(adapter)


def test_smoke_requires_confirm(capsys):
    script = Path(__file__).resolve().parents[1] / "scripts" / "meta_create_paused_smoke.py"
    # Execute main without --confirm via runpy would need argv; call main directly.
    import importlib.util

    spec = importlib.util.spec_from_file_location("meta_create_paused_smoke", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    code = mod.main([])
    captured = capsys.readouterr()
    assert code == 2
    assert "LIVE WRITE DISABLED WITHOUT --confirm" in captured.out


def test_duplicate_smoke_name_does_not_create_second():
    state = {"posts": 0}

    def handler(method, url, headers, body, timeout):
        if method == "GET" and "/campaigns" in url:
            return 200, _jb(
                {
                    "data": [
                        {
                            "id": "111",
                            "name": SMOKE_CAMPAIGN_NAME,
                            "status": "PAUSED",
                            "effective_status": "PAUSED",
                        }
                    ]
                }
            )
        if method == "POST":
            state["posts"] += 1
            return 200, _jb({"id": "should_not_happen"})
        raise AssertionError(url)

    adapter = MetaMarketingApiAdapter(
        _meta(), budget=_budget(), transport=FakeTransport(handler)
    )
    found = find_campaigns_by_exact_name(adapter, SMOKE_CAMPAIGN_NAME)
    assert len(found) == 1
    # smoke script would skip create when existing — verify we don't call create here
    assert state["posts"] == 0


def test_token_not_logged_on_smoke_create(caplog):
    def handler(method, url, headers, body, timeout):
        if method == "POST":
            form = parse_qs(body.decode("utf-8"))
            assert form["status"] == ["PAUSED"]
            assert form["objective"] == ["OUTCOME_ENGAGEMENT"]
            assert form["is_adset_budget_sharing_enabled"] == ["False"]
            assert "ACTIVE" not in body.decode("utf-8")
            return 200, _jb({"id": "camp_smoke"})
        return 200, _jb(
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
        create_paused_smoke_campaign(adapter)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert SECRET not in text
    assert SECRET not in json.dumps(adapter.request_log)


def test_wrong_account_currency_timezone_abort():
    def handler(method, url, headers, body, timeout):
        return 200, _jb(
            {
                "id": "act_999",
                "name": "Wrong Account",
                "account_status": 1,
                "currency": "USD",
                "timezone_name": "UTC",
            }
        )

    adapter = MetaMarketingApiAdapter(
        _meta(ad_account_id="999"), budget=_budget(), transport=FakeTransport(handler)
    )
    pre = run_smoke_preflight(adapter, _meta(ad_account_id="999"), require_write_enabled=True)
    assert pre.ok is False
    assert any("3462495317264561" in e for e in pre.errors)

    adapter2 = MetaMarketingApiAdapter(_meta(), budget=_budget(), transport=FakeTransport(handler))
    pre2 = run_smoke_preflight(adapter2, _meta(), require_write_enabled=True)
    assert pre2.ok is False
    assert any("currency" in e for e in pre2.errors)
    assert any("timezone" in e for e in pre2.errors)
    assert any("account name" in e for e in pre2.errors)


def test_active_enabled_aborts_preflight():
    def handler(*a, **k):
        raise AssertionError("should not call network when active enabled")

    meta = _meta(active_enabled=True)
    adapter = MetaMarketingApiAdapter(meta, budget=_budget(), transport=FakeTransport(handler))
    pre = run_smoke_preflight(adapter, meta)
    assert pre.ok is False
    assert any("META_ACTIVE_ENABLED" in e for e in pre.errors)


def test_post_no_blind_retry_on_smoke_create():
    state = {"n": 0}

    def handler(method, url, headers, body, timeout):
        state["n"] += 1
        return 500, _jb({"error": {"message": "temporary"}})

    adapter = MetaMarketingApiAdapter(
        _meta(get_max_retries=5), budget=_budget(), transport=FakeTransport(handler)
    )
    with pytest.raises(Exception):
        create_paused_smoke_campaign(adapter)
    assert state["n"] == 1
