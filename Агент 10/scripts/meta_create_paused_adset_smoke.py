#!/usr/bin/env python3
"""Controlled LIVE write smoke-test: create ONE PAUSED Meta Ad Set.

Parent campaign (must already exist, PAUSED):
  120252824987200202 / OPENHOME_AGENT10_SMOKE_TEST_2026_08_09

Creates Ad Set only — no Creative / Ad / ACTIVE.

Usage:
  PYTHONPATH=src python3 scripts/meta_create_paused_adset_smoke.py
  PYTHONPATH=src python3 scripts/meta_create_paused_adset_smoke.py --confirm
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _load_dotenv(path: Path) -> None:
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


def _set_env_file_key(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []
    out: list[str] = []
    found = False
    for line in lines:
        raw = line.rstrip("\n")
        if raw.strip().startswith("#") or "=" not in raw:
            out.append(line if line.endswith("\n") else line + "\n")
            continue
        k, _, _ = raw.partition("=")
        if k.strip() == key:
            out.append(f"{key}={value}\n")
            found = True
        else:
            out.append(line if line.endswith("\n") else line + "\n")
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(f"{key}={value}\n")
    path.write_text("".join(out), encoding="utf-8")


def _read_env_flag(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Meta PAUSED ad set smoke-test (explicit confirm)")
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Required to perform the single live POST create",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env_path = _ROOT / ".env"
    _load_dotenv(env_path)

    from agent10_marketer.adapters.meta_ads import MetaMarketingApiAdapter
    from agent10_marketer.adapters.meta_errors import MetaApiError, MetaSafetyError
    from agent10_marketer.config import load_budget_config, load_meta_config
    from agent10_marketer.meta_smoke import (
        SMOKE_ADSET_DAILY_BUDGET_THB,
        SMOKE_ADSET_DURATION_DAYS,
        SMOKE_ADSET_NAME,
        SMOKE_BID_STRATEGY,
        SMOKE_BILLING_EVENT,
        SMOKE_CAMPAIGN_ID,
        SMOKE_CAMPAIGN_NAME,
        SMOKE_DESTINATION_TYPE,
        SMOKE_OPTIMIZATION_GOAL,
        SMOKE_TARGETING,
        create_paused_smoke_adset,
        find_adsets_by_exact_name,
        get_adset,
        get_campaign,
        run_adset_smoke_preflight,
    )

    if not args.confirm:
        print("LIVE WRITE DISABLED WITHOUT --confirm")
        print(
            "Usage: PYTHONPATH=src python3 scripts/meta_create_paused_adset_smoke.py --confirm"
        )
        return 2

    print("=== Meta PAUSED ad set smoke-test ===")
    print(f"Campaign ID:   {SMOKE_CAMPAIGN_ID}")
    print(f"Campaign name: {SMOKE_CAMPAIGN_NAME}")
    print(f"Ad Set name:   {SMOKE_ADSET_NAME}")
    print(f"Daily budget:  {SMOKE_ADSET_DAILY_BUDGET_THB} THB")
    print(f"Duration:      {SMOKE_ADSET_DURATION_DAYS} days (Asia/Bangkok)")
    print(f"Optimization:  {SMOKE_OPTIMIZATION_GOAL}")
    print(f"Billing:       {SMOKE_BILLING_EVENT}")
    print("Status:        PAUSED (forced)")
    print()

    try:
        _set_env_file_key(env_path, "META_WRITE_ENABLED", "true")
        _set_env_file_key(env_path, "META_ACTIVE_ENABLED", "false")
        os.environ["META_WRITE_ENABLED"] = "true"
        os.environ["META_ACTIVE_ENABLED"] = "false"

        meta = load_meta_config()
        budget = load_budget_config(_ROOT / "config")
        adapter = MetaMarketingApiAdapter(meta, budget=budget)

        print("Write safety before create:")
        print(f"  META_WRITE_ENABLED={meta.write_enabled}")
        print(f"  META_ACTIVE_ENABLED={meta.active_enabled}")
        print(f"  META_ADS_ENABLED={meta.ads_enabled}")
        print(f"  Token: {'SET' if meta.token_set else 'MISSING'}")
        print()

        pre = run_adset_smoke_preflight(adapter, meta, require_write_enabled=True)
        if not pre.ok:
            print("Preflight: FAIL")
            for err in pre.errors:
                print(f"  - {err}")
            return 3

        print("Preflight: PASS")
        print(f"Account: {pre.account.get('name') if pre.account else None}")
        print(f"Campaign status: {pre.campaign.get('status') if pre.campaign else None}")
        print(f"Campaign objective: {pre.campaign.get('objective') if pre.campaign else None}")
        print(f"Housing/Special Ad Category: {pre.housing_status}")
        print()

        existing = find_adsets_by_exact_name(adapter, SMOKE_ADSET_NAME)
        created = False
        if len(existing) > 1:
            print("FAIL: duplicate smoke ad sets already present:")
            for row in existing:
                print(f"  id={row.get('id')} name={row.get('name')}")
            return 4
        if len(existing) == 1:
            adset_id = str(existing[0]["id"])
            print("SMOKE AD SET ALREADY EXISTS")
            print(f"Ad Set ID: {adset_id}")
            print("Duplicate protection: PASS (no create)")
        else:
            print("Creating ONE PAUSED ad set (single POST)...")
            result = create_paused_smoke_adset(adapter, meta)
            adset_id = str(result.get("id") or "")
            if not adset_id:
                print("FAIL: create returned no id:", result)
                return 5
            created = True
            print("Created: YES")
            print(f"Ad Set ID: {adset_id}")
            after = find_adsets_by_exact_name(adapter, SMOKE_ADSET_NAME)
            if len(after) != 1:
                print(f"FAIL: expected exactly 1 ad set named {SMOKE_ADSET_NAME}, found {len(after)}")
                return 6
            print("Duplicate protection: PASS")

        verified = get_adset(adapter, adset_id)
        camp = get_campaign(adapter, SMOKE_CAMPAIGN_ID)
        print()
        print("GET verify:")
        print(f"  id:               {verified.get('id')}")
        print(f"  name:             {verified.get('name')}")
        print(f"  campaign_id:      {verified.get('campaign_id')}")
        print(f"  status:           {verified.get('status')}")
        print(f"  effective_status: {verified.get('effective_status')}")
        print(f"  daily_budget:     {verified.get('daily_budget')}")
        print(f"  lifetime_budget:  {verified.get('lifetime_budget')}")
        print(f"  optimization:     {verified.get('optimization_goal')}")
        print(f"  billing_event:    {verified.get('billing_event')}")
        print(f"  destination_type: {verified.get('destination_type')}")
        print(f"  start_time:       {verified.get('start_time')}")
        print(f"  end_time:         {verified.get('end_time')}")
        print(f"  targeting:        {verified.get('targeting')}")
        print(f"  bid_strategy:     {SMOKE_BID_STRATEGY}")
        print(f"  placements:       Advantage+/automatic (publisher_platforms omitted)")
        print(f"  parent campaign:  {camp.get('status')} / {camp.get('effective_status')}")

        if verified.get("name") != SMOKE_ADSET_NAME:
            print(f"FAIL: name mismatch {verified.get('name')!r}")
            return 7
        if str(verified.get("campaign_id")) != SMOKE_CAMPAIGN_ID:
            print(f"FAIL: campaign_id mismatch {verified.get('campaign_id')!r}")
            return 8
        if str(verified.get("status") or "").upper() != "PAUSED":
            print(f"FAIL: status is not PAUSED: {verified.get('status')!r}")
            return 9
        if str(camp.get("status") or "").upper() != "PAUSED":
            print(f"FAIL: parent campaign no longer PAUSED: {camp.get('status')!r}")
            return 10

        methods = [e["method"] for e in adapter.request_log]
        post_count = sum(1 for m in methods if m == "POST")
        if created and post_count != 1:
            print(f"FAIL: expected exactly 1 POST when created, got {post_count}: {methods}")
            return 11
        if not created and post_count != 0:
            print(f"FAIL: expected 0 POST when existing, got {post_count}: {methods}")
            return 12
        if any(m in {"PATCH", "DELETE", "PUT"} for m in methods):
            print(f"FAIL: unexpected mutating methods: {methods}")
            return 13

        print()
        print("Creative created: NO")
        print("Ad created: NO")
        print("Spend possible: NO (campaign+adset PAUSED, no ad)")
        print(f"Targeting summary: geo=TH only → {SMOKE_TARGETING}")
        print(f"Destination: {SMOKE_DESTINATION_TYPE}")
        print(f"Housing/Special Ad Category: {pre.housing_status}")
        print()
        print("LIVE PAUSED AD SET SMOKE TEST PASSED")
        return 0

    except (MetaApiError, MetaSafetyError) as exc:
        print(f"FAILED: {exc}")
        return 1
    finally:
        _set_env_file_key(env_path, "META_WRITE_ENABLED", "false")
        _set_env_file_key(env_path, "META_ACTIVE_ENABLED", "false")
        os.environ["META_WRITE_ENABLED"] = "false"
        os.environ["META_ACTIVE_ENABLED"] = "false"
        print()
        print("After test safety restored:")
        print(f"  META_WRITE_ENABLED={_read_env_flag(env_path, 'META_WRITE_ENABLED')}")
        print(f"  META_ACTIVE_ENABLED={_read_env_flag(env_path, 'META_ACTIVE_ENABLED')}")
        print(f"  META_ADS_ENABLED={_read_env_flag(env_path, 'META_ADS_ENABLED')}")


if __name__ == "__main__":
    raise SystemExit(main())
