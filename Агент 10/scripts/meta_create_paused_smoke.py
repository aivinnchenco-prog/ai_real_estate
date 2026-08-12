#!/usr/bin/env python3
"""Controlled LIVE write smoke-test: create ONE PAUSED Meta campaign.

Requires explicit --confirm.
Creates campaign only — no ad set / creative / ad / ACTIVE / budget.

Usage:
  PYTHONPATH=src python3 scripts/meta_create_paused_smoke.py
  PYTHONPATH=src python3 scripts/meta_create_paused_smoke.py --confirm
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
    """Update or append a single KEY=value in .env without touching other secrets."""
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
    p = argparse.ArgumentParser(description="Meta PAUSED campaign smoke-test (explicit confirm)")
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Required to perform the single live POST create",
    )
    p.add_argument(
        "--name",
        default=None,
        help="Override smoke campaign name (default fixed smoke name)",
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
        SMOKE_CAMPAIGN_NAME,
        SMOKE_OBJECTIVE,
        create_paused_smoke_campaign,
        find_campaigns_by_exact_name,
        get_campaign,
        run_smoke_preflight,
    )

    if not args.confirm:
        print("LIVE WRITE DISABLED WITHOUT --confirm")
        print("Usage: PYTHONPATH=src python3 scripts/meta_create_paused_smoke.py --confirm")
        return 2

    smoke_name = (args.name or SMOKE_CAMPAIGN_NAME).strip()
    print("=== Meta PAUSED campaign smoke-test ===")
    print(f"Campaign name: {smoke_name}")
    print(f"Objective:     {SMOKE_OBJECTIVE}")
    print("Status:        PAUSED (forced)")
    print()

    # Temporarily ensure write enabled for this controlled run (restore in finally).
    previous_write = _read_env_flag(env_path, "META_WRITE_ENABLED") or "false"
    write_restored = False
    try:
        if previous_write.lower() not in {"1", "true", "yes", "on"}:
            _set_env_file_key(env_path, "META_WRITE_ENABLED", "true")
            os.environ["META_WRITE_ENABLED"] = "true"
            print("Temporarily set META_WRITE_ENABLED=true for smoke-test")
        else:
            os.environ["META_WRITE_ENABLED"] = "true"

        # Force ACTIVE off in process + file
        _set_env_file_key(env_path, "META_ACTIVE_ENABLED", "false")
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

        pre = run_smoke_preflight(adapter, meta, require_write_enabled=True)
        if not pre.ok:
            print("Preflight: FAIL")
            for err in pre.errors:
                print(f"  - {err}")
            return 3
        assert pre.account is not None
        print("Preflight: PASS")
        print(f"Account: {pre.account.get('name')}")
        print(f"Ad Account ID: {meta.ad_account_id}")
        print(f"Currency: {pre.account.get('currency')}")
        print(f"Timezone: {pre.account.get('timezone_name')}")
        print()

        existing = find_campaigns_by_exact_name(adapter, smoke_name)
        created = False
        if len(existing) > 1:
            print("FAIL: duplicate smoke campaigns already present:")
            for c in existing:
                print(f"  id={c.get('id')} name={c.get('name')}")
            return 4
        if len(existing) == 1:
            campaign_id = str(existing[0]["id"])
            print("SMOKE CAMPAIGN ALREADY EXISTS")
            print(f"Campaign ID: {campaign_id}")
            print("Duplicate protection: PASS (no create)")
        else:
            print("Creating ONE PAUSED campaign (single POST)...")
            result = create_paused_smoke_campaign(adapter, name=smoke_name)
            campaign_id = str(result.get("id") or "")
            if not campaign_id:
                print("FAIL: create returned no id:", result)
                return 5
            created = True
            print(f"Created: YES")
            print(f"Campaign ID: {campaign_id}")

            # Ensure we did not somehow create duplicates in this run
            after = find_campaigns_by_exact_name(adapter, smoke_name)
            if len(after) != 1:
                print(f"FAIL: expected exactly 1 campaign named {smoke_name}, found {len(after)}")
                return 6
            print("Duplicate protection: PASS")

        # Live verify GET
        verified = get_campaign(adapter, campaign_id)
        print()
        print("GET verify:")
        print(f"  id:               {verified.get('id')}")
        print(f"  name:             {verified.get('name')}")
        print(f"  objective:        {verified.get('objective')}")
        print(f"  status:           {verified.get('status')}")
        print(f"  effective_status: {verified.get('effective_status')}")

        if verified.get("name") != smoke_name:
            print(f"FAIL: name mismatch {verified.get('name')!r} != {smoke_name!r}")
            return 7
        if str(verified.get("status") or "").upper() != "PAUSED":
            print(f"FAIL: status is not PAUSED: {verified.get('status')!r}")
            return 8

        # Account campaigns list contains exactly one with this name
        listed = find_campaigns_by_exact_name(adapter, smoke_name)
        if len(listed) != 1:
            print(f"FAIL: list_campaigns name count={len(listed)}")
            return 9

        # Safety: only GET + at most one POST in this process
        methods = [e["method"] for e in adapter.request_log]
        post_count = sum(1 for m in methods if m == "POST")
        if created and post_count != 1:
            print(f"FAIL: expected exactly 1 POST when created, got {post_count}: {methods}")
            return 10
        if not created and post_count != 0:
            print(f"FAIL: expected 0 POST when existing, got {post_count}: {methods}")
            return 11
        if any(m in {"PATCH", "DELETE", "PUT"} for m in methods):
            print(f"FAIL: unexpected mutating methods: {methods}")
            return 12

        print()
        print("Ad Set created: NO")
        print("Creative created: NO")
        print("Ad created: NO")
        print("Spend possible: NO (PAUSED campaign only, no adset/ad)")
        print()
        print("LIVE PAUSED CAMPAIGN SMOKE TEST PASSED")
        return 0

    except (MetaApiError, MetaSafetyError) as exc:
        print(f"FAILED: {exc}")
        return 1
    finally:
        # Always restore read-only write switch
        _set_env_file_key(env_path, "META_WRITE_ENABLED", "false")
        _set_env_file_key(env_path, "META_ACTIVE_ENABLED", "false")
        os.environ["META_WRITE_ENABLED"] = "false"
        os.environ["META_ACTIVE_ENABLED"] = "false"
        write_restored = True
        print()
        print("After test safety restored:")
        print(f"  META_WRITE_ENABLED={_read_env_flag(env_path, 'META_WRITE_ENABLED')}")
        print(f"  META_ACTIVE_ENABLED={_read_env_flag(env_path, 'META_ACTIVE_ENABLED')}")
        print(f"  META_ADS_ENABLED={_read_env_flag(env_path, 'META_ADS_ENABLED')}")
        assert write_restored


if __name__ == "__main__":
    raise SystemExit(main())
