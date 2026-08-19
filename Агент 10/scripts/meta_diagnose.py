#!/usr/bin/env python3
"""Read-only Meta Marketing API diagnostic for Agent 10.

GET only. Never prints META_ACCESS_TOKEN.
Never creates campaigns/ads.

Usage:
  cd "Агент 10"
  PYTHONPATH=src python3 scripts/meta_diagnose.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running without installing the package.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no python-dotenv dependency). Does not overwrite set vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _account_name_ok(actual: str | None, expected: str) -> bool:
    if not actual or not expected:
        return False
    a = actual.strip().lower()
    e = expected.strip().lower()
    return e in a or a in e


def main() -> int:
    _load_dotenv(_ROOT / ".env")

    from agent10_marketer.adapters.meta_ads import MetaMarketingApiAdapter
    from agent10_marketer.adapters.meta_errors import MetaApiError, MetaTokenMissing
    from agent10_marketer.config import load_budget_config, load_meta_config

    meta = load_meta_config()
    budget = load_budget_config(_ROOT / "config")
    adapter = MetaMarketingApiAdapter(meta, budget=budget)

    print("=== Agent 10 Meta Marketing API diagnostic (GET only) ===")
    print(f"Graph API version: {meta.graph_api_version}")
    print(f"App ID:            {meta.app_id}")
    print(f"Business ID:       {meta.business_id}")
    print(f"Ad Account ID:     {meta.ad_account_id}")
    print(f"Page ID:           {meta.page_id or '(not set)'}")
    print(f"Instagram ID:      {meta.instagram_account_id or '(not set)'}")
    print(f"Token:             {'SET' if meta.token_set else 'MISSING'}")
    print(f"META_WRITE_ENABLED={meta.write_enabled}  META_ACTIVE_ENABLED={meta.active_enabled}")
    print()

    if not meta.token_set:
        print("Meta API: FAILED")
        print("META_TOKEN_MISSING: set META_ACCESS_TOKEN in local .env (never commit).")
        return 2

    # Safety: expected account id must match local contract.
    if meta.ad_account_id != "3462495317264561":
        level = "FAIL" if meta.diagnostic_strict else "WARNING"
        print(
            f"{level}: META_AD_ACCOUNT_ID={meta.ad_account_id} "
            "(expected 3462495317264561)"
        )
        if meta.diagnostic_strict:
            print("Meta API: FAILED")
            return 3

    methods_used: list[str] = []
    try:
        account = adapter.get_ad_account()
        methods_used = [e["method"] for e in adapter.request_log]
        print("Meta API: CONNECTED")
        print()
        print("Account:")
        print(f"  name:      {account.get('name')}")
        print(f"  status:    {account.get('account_status')}")
        print(f"  currency:  {account.get('currency')}")
        print(f"  timezone:  {account.get('timezone_name')}")
        print()

        # Safety assertions
        name_ok = _account_name_ok(account.get("name"), meta.expected_account_name)
        cur_ok = (account.get("currency") or "").upper() == meta.expected_currency.upper()
        tz_ok = (account.get("timezone_name") or "") == meta.expected_timezone

        def _check(label: str, ok: bool, actual: object, expected: str) -> None:
            if ok:
                print(f"CHECK {label}: OK ({actual})")
                return
            level = "FAIL" if meta.diagnostic_strict else "WARNING"
            print(f"CHECK {label}: {level} actual={actual!r} expected≈{expected!r}")

        _check("account_name", name_ok, account.get("name"), meta.expected_account_name)
        _check("currency", cur_ok, account.get("currency"), meta.expected_currency)
        _check("timezone", tz_ok, account.get("timezone_name"), meta.expected_timezone)

        if meta.diagnostic_strict and not (name_ok and cur_ok and tz_ok):
            print()
            print("Meta API: FAILED (strict diagnostic checks)")
            return 4

        # Campaigns
        try:
            campaigns = adapter.list_campaigns(limit=25)
            print()
            print(f"Campaign access: OK")
            print(f"Campaign count:  {len(campaigns)}")
        except MetaApiError as exc:
            print()
            print(f"Campaign access: FAIL — {exc}")

        # Insights
        try:
            insights = adapter.get_account_insights(date_preset="last_7d")
            print()
            print("Insights access: OK")
            print(
                "  spend={spend} impressions={impressions} reach={reach} "
                "clicks={clicks} ctr={ctr} cpc={cpc} cpm={cpm}".format(**insights.to_dict())
            )
        except MetaApiError as exc:
            print()
            print(f"Insights access: FAIL — {exc}")

        methods_used = [e["method"] for e in adapter.request_log]
        if any(m != "GET" for m in methods_used):
            print()
            print("FAIL: diagnostic issued non-GET methods:", methods_used)
            return 5
        print()
        print(f"Requests: {len(adapter.request_log)} GET-only (write switches untouched)")
        print("Page ID configured:", "yes" if meta.page_id else "no")
        print("Instagram ID configured:", "yes" if meta.instagram_account_id else "no")
        return 0

    except MetaTokenMissing as exc:
        print("Meta API: FAILED")
        print(str(exc))
        return 2
    except MetaApiError as exc:
        print("Meta API: FAILED")
        print(str(exc))
        if getattr(exc, "fbtrace_id", None):
            print(f"fbtrace_id: {exc.fbtrace_id}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
