"""Offline tests for Facebook existing-post creative smoke."""

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
    MetaSafetyError,
    MetaWriteDisabled,
)
from agent10_marketer.adapters.meta_policy import force_paused_status, validate_object_story_id
from agent10_marketer.config import BudgetConfig, MetaConfig
from agent10_marketer.meta_smoke import (
    SMOKE_FB_CREATIVE_NAME,
    audit_instagram_existing_post,
    create_paused_smoke_fb_creative,
    find_adcreatives_by_exact_name,
)

SECRET = "FB_CREATIVE_SMOKE_TOKEN_DO_NOT_LOG"
PAGE_TOKEN = "PAGE_TOKEN_DO_NOT_LOG"
STORY = "1189108177625326_122105052381405477"


def _meta(**overrides: Any) -> MetaConfig:
    base = dict(
        app_id="1051487844031310",
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
        get_max_retries=1,
    )
    base.update(overrides)
    return MetaConfig(**base)


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method, url, headers, body, timeout):
        parsed = urlparse(url)
        self.calls.append({"method": method, "path": parsed.path, "body": body, "url": url})
        return self.handler(method, url, headers, body, timeout)


def _jb(obj: Any) -> bytes:
    return json.dumps(obj).encode("utf-8")


def test_no_confirm_blocks_script(capsys):
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "meta_create_fb_creative_smoke.py"
    )
    spec = importlib.util.spec_from_file_location("meta_create_fb_creative_smoke", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    code = mod.main([])
    assert code == 2
    assert "LIVE WRITE DISABLED WITHOUT --confirm" in capsys.readouterr().out


def test_no_confirmed_fb_post_no_creative_post():
    with pytest.raises(MetaSafetyError, match="no confirmed FB post"):
        # empty story blocked before network
        adapter = MetaMarketingApiAdapter(
            _meta(),
            budget=BudgetConfig(),
            transport=FakeTransport(lambda *a, **k: (_ for _ in ()).throw(AssertionError())),
        )
        create_paused_smoke_fb_creative(adapter, object_story_id="")


def test_malformed_object_story_id_blocked():
    with pytest.raises(MetaSafetyError, match="malformed"):
        validate_object_story_id("https://www.facebook.com/posts/123")
    with pytest.raises(MetaSafetyError, match="malformed"):
        validate_object_story_id("not_a_story")
    with pytest.raises(MetaSafetyError, match="required"):
        validate_object_story_id(None)


def test_write_false_blocks_creative():
    def handler(*a, **k):
        raise AssertionError("blocked")

    adapter = MetaMarketingApiAdapter(
        _meta(write_enabled=False), budget=BudgetConfig(), transport=FakeTransport(handler)
    )
    with pytest.raises(MetaWriteDisabled):
        adapter.create_creative_from_existing_facebook_post(
            name=SMOKE_FB_CREATIVE_NAME, object_story_id=STORY
        )


def test_active_true_abort_policy():
    with pytest.raises(MetaActiveDisabled):
        force_paused_status("ACTIVE", meta=_meta(active_enabled=False))


def test_duplicate_creative_detected_without_create():
    state = {"posts": 0}

    def handler(method, url, headers, body, timeout):
        if method == "GET" and "/adcreatives" in url:
            return 200, _jb(
                {"data": [{"id": "9", "name": SMOKE_FB_CREATIVE_NAME, "object_story_id": STORY}]}
            )
        if method == "POST":
            state["posts"] += 1
            return 200, _jb({"id": "x"})
        raise AssertionError(url)

    adapter = MetaMarketingApiAdapter(
        _meta(), budget=BudgetConfig(), transport=FakeTransport(handler)
    )
    found = find_adcreatives_by_exact_name(adapter, SMOKE_FB_CREATIVE_NAME)
    assert len(found) == 1
    assert state["posts"] == 0


def test_token_not_logged_and_one_creative_post(caplog):
    state = {"posts": 0}

    def handler(method, url, headers, body, timeout):
        path = urlparse(url).path
        if method == "GET" and path.endswith("/1189108177625326"):
            return 200, _jb({"id": "1189108177625326", "access_token": PAGE_TOKEN})
        if method == "GET" and STORY in path:
            return 200, _jb({"id": STORY, "created_time": "2026-08-07T04:51:49+0000"})
        if method == "POST" and path.endswith("/adcreatives"):
            state["posts"] += 1
            form = parse_qs(body.decode("utf-8"))
            assert form["object_story_id"] == [STORY]
            assert form["name"] == [SMOKE_FB_CREATIVE_NAME]
            return 200, _jb({"id": "creative_1"})
        if method == "POST" and path.endswith("/ads"):
            raise AssertionError("Ad POST forbidden")
        raise AssertionError(f"{method} {path}")

    transport = FakeTransport(handler)
    adapter = MetaMarketingApiAdapter(_meta(), budget=BudgetConfig(), transport=transport)
    with caplog.at_level(logging.DEBUG):
        result = create_paused_smoke_fb_creative(adapter, object_story_id=STORY)
    assert result["id"] == "creative_1"
    assert state["posts"] == 1
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert SECRET not in joined
    assert PAGE_TOKEN not in joined
    assert SECRET not in json.dumps(adapter.request_log)
    assert not any(c["method"] == "POST" and c["path"].endswith("/ads") for c in transport.calls)


def test_page_access_failure_handled():
    def handler(method, url, headers, body, timeout):
        return 403, _jb(
            {"error": {"message": "Requires pages_read_engagement", "code": 10, "type": "OAuthException"}}
        )

    adapter = MetaMarketingApiAdapter(
        _meta(), budget=BudgetConfig(), transport=FakeTransport(handler)
    )
    with pytest.raises(Exception):
        adapter.get_page_access_token()


def test_unsupported_field_fallback_on_get_creative():
    state = {"n": 0}

    def handler(method, url, headers, body, timeout):
        state["n"] += 1
        if "thumbnail_url" in url:
            return 400, _jb(
                {
                    "error": {
                        "message": "(#100) Tried to get nonexisting field (thumbnail_url)",
                        "code": 100,
                        "type": "OAuthException",
                    }
                }
            )
        return 200, _jb(
            {
                "id": "c1",
                "name": SMOKE_FB_CREATIVE_NAME,
                "object_story_id": STORY,
                "status": "ACTIVE",
                "effective_object_story_id": STORY,
            }
        )

    adapter = MetaMarketingApiAdapter(
        _meta(), budget=BudgetConfig(), transport=FakeTransport(handler)
    )
    got = adapter.get_adcreative("c1")
    assert got["id"] == "c1"
    assert got.get("thumbnail_url") is None
    assert state["n"] == 2


def test_permission_1487194_classified():
    from agent10_marketer.adapters.meta_errors import MetaPermissionDenied, classify_meta_error

    err = classify_meta_error(
        http_status=400,
        payload={
            "error": {
                "message": "Permissions error",
                "type": "OAuthException",
                "code": 200,
                "error_subcode": 1487194,
                "error_user_msg": "Either the object you are trying to access is not visible...",
            }
        },
    )
    assert isinstance(err, MetaPermissionDenied)
    assert "pages_manage_ads" in str(err) or "1487194" in str(err)

    audit = audit_instagram_existing_post(_meta())
    assert audit["status"] == "BLOCKED"
    adapter = MetaMarketingApiAdapter(
        _meta(),
        budget=BudgetConfig(),
        transport=FakeTransport(lambda *a, **k: (_ for _ in ()).throw(AssertionError())),
    )
    with pytest.raises(MetaSafetyError, match="TODO"):
        adapter.create_creative_from_existing_instagram_media(instagram_media_id="1")
