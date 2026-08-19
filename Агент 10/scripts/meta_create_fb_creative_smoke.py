#!/usr/bin/env python3
"""Controlled LIVE write smoke-test: Ad Creative from existing Facebook post.

Requires:
  --confirm
  --object-story-id <Meta-confirmed PAGEID_POSTID>

Creates Creative only — no Ad / ACTIVE / budget changes.

Usage:
  PYTHONPATH=src python3 scripts/meta_find_facebook_posts.py
  PYTHONPATH=src python3 scripts/meta_create_fb_creative_smoke.py --confirm \\
    --object-story-id 1189108177625326_122105052381405477
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
    p = argparse.ArgumentParser(
        description="Meta FB existing-post creative smoke-test (explicit confirm)"
    )
    p.add_argument("--confirm", action="store_true")
    p.add_argument(
        "--object-story-id",
        default="",
        help="Meta-confirmed Facebook post id (PAGEID_POSTID from published_posts.id)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env_path = _ROOT / ".env"
    _load_dotenv(env_path)

    from agent10_marketer.adapters.meta_ads import MetaMarketingApiAdapter
    from agent10_marketer.adapters.meta_errors import MetaApiError, MetaSafetyError
    from agent10_marketer.adapters.meta_policy import validate_object_story_id
    from agent10_marketer.config import load_budget_config, load_meta_config
    from agent10_marketer.meta_smoke import (
        SMOKE_ADSET_ID,
        SMOKE_CAMPAIGN_ID,
        SMOKE_FB_CREATIVE_NAME,
        audit_instagram_existing_post,
        create_paused_smoke_fb_creative,
        find_adcreatives_by_exact_name,
        get_adset,
        get_campaign,
        run_smoke_preflight,
    )

    if not args.confirm:
        print("LIVE WRITE DISABLED WITHOUT --confirm")
        print(
            "Usage: PYTHONPATH=src python3 scripts/meta_create_fb_creative_smoke.py "
            "--confirm --object-story-id <PAGEID_POSTID>"
        )
        return 2

    if not str(args.object_story_id or "").strip():
        print("FAILED: --object-story-id required (no confirmed FB post → no creative POST)")
        print("Run: PYTHONPATH=src python3 scripts/meta_find_facebook_posts.py")
        return 3

    print("=== Meta Facebook existing-post creative smoke-test ===")
    print(f"Creative name: {SMOKE_FB_CREATIVE_NAME}")
    print(f"Requested object_story_id: {args.object_story_id}")
    print("Creates: Ad Creative only (no Ad)")
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

        if meta.active_enabled:
            print("ABORT: META_ACTIVE_ENABLED=true")
            return 4

        pre = run_smoke_preflight(adapter, meta, require_write_enabled=True)
        if not pre.ok:
            print("Preflight: FAIL")
            for err in pre.errors:
                print(f"  - {err}")
            return 5

        page = adapter.get_page()
        print("Preflight: PASS")
        print(f"Facebook Page: {page.get('name')} / {page.get('id')}")
        print()

        # Confirm post via Meta GET (Page token) — never invent id.
        try:
            validate_object_story_id(args.object_story_id)
            confirmed = adapter.confirm_facebook_object_story_id(args.object_story_id)
        except MetaSafetyError as exc:
            print(f"FAILED: {exc}")
            return 6

        from dataclasses import replace

        page_token = adapter.get_page_access_token()
        original = adapter.meta.access_token
        try:
            adapter.meta = replace(adapter.meta, access_token=page_token)
            post = adapter._get(
                f"/{confirmed}",
                fields="id,created_time,message,permalink_url,is_published",
            )
        finally:
            adapter.meta = replace(adapter.meta, access_token=original)

        msg = (post.get("message") or "").replace("\n", " ")
        snippet = msg[:140] + ("…" if len(msg) > 140 else "")
        print("Selected existing post:")
        print(f"  post ID / object_story_id: {confirmed}")
        print(f"  created_time: {post.get('created_time')}")
        print(f"  permalink:    {post.get('permalink_url')}")
        print(f"  message:      {snippet!r}")
        print("  object_id mapping: unknown (Publication Ledger has no Facebook rows)")
        print()

        # Parent campaign/adset remain untouched (read verify only).
        camp = get_campaign(adapter, SMOKE_CAMPAIGN_ID)
        adset = get_adset(adapter, SMOKE_ADSET_ID)
        print(f"Campaign {SMOKE_CAMPAIGN_ID}: {camp.get('status')} / {camp.get('effective_status')}")
        print(f"Ad Set   {SMOKE_ADSET_ID}: {adset.get('status')} / {adset.get('effective_status')}")
        print()

        existing = find_adcreatives_by_exact_name(adapter, SMOKE_FB_CREATIVE_NAME)
        created = False
        if len(existing) > 1:
            print("FAIL: duplicate smoke creatives present")
            return 7
        if len(existing) == 1:
            creative_id = str(existing[0]["id"])
            print("SMOKE CREATIVE ALREADY EXISTS")
            print(f"Creative ID: {creative_id}")
            print("Duplicate protection: PASS (no create)")
        else:
            print("Creating ONE Ad Creative (single POST)...")
            # Reset request log tracking for write count clarity after reads.
            before_posts = sum(1 for e in adapter.request_log if e["method"] == "POST")
            result = create_paused_smoke_fb_creative(
                adapter, object_story_id=confirmed, name=SMOKE_FB_CREATIVE_NAME
            )
            creative_id = str(result.get("id") or "")
            if not creative_id:
                print("FAIL: create returned no id:", result)
                return 8
            created = True
            after_posts = sum(1 for e in adapter.request_log if e["method"] == "POST")
            if after_posts - before_posts != 1:
                print(f"FAIL: expected exactly 1 new POST, delta={after_posts - before_posts}")
                return 9
            print("Created: YES")
            print(f"Creative ID: {creative_id}")
            print("Duplicate protection: PASS")

        verified = adapter.get_adcreative(creative_id)
        print()
        print("GET verify:")
        print(f"  id:                       {verified.get('id')}")
        print(f"  name:                     {verified.get('name')}")
        print(f"  object_story_id:          {verified.get('object_story_id')}")
        print(f"  effective_object_story_id:{verified.get('effective_object_story_id')}")
        print(f"  status:                   {verified.get('status')}")
        print(f"  thumbnail_url:            {verified.get('thumbnail_url')}")

        if verified.get("name") != SMOKE_FB_CREATIVE_NAME:
            print("FAIL: creative name mismatch")
            return 10
        got_story = verified.get("object_story_id") or verified.get("effective_object_story_id")
        if got_story and got_story != confirmed:
            print(f"FAIL: object_story_id mismatch {got_story!r} != {confirmed!r}")
            return 11

        camp2 = get_campaign(adapter, SMOKE_CAMPAIGN_ID)
        adset2 = get_adset(adapter, SMOKE_ADSET_ID)
        if str(camp2.get("status") or "").upper() != "PAUSED":
            print(f"FAIL: campaign status changed: {camp2.get('status')!r}")
            return 12
        if str(adset2.get("status") or "").upper() != "PAUSED":
            print(f"FAIL: ad set status changed: {adset2.get('status')!r}")
            return 13

        methods = [e["method"] for e in adapter.request_log]
        if any(m in {"PATCH", "DELETE", "PUT"} for m in methods):
            print(f"FAIL: unexpected mutating methods: {methods}")
            return 14
        # Ensure we never called ads create endpoint.
        paths = [e.get("path", "") for e in adapter.request_log]
        if any(p.endswith("/ads") and e["method"] == "POST" for e, p in zip(adapter.request_log, paths)):
            print("FAIL: Ad POST detected")
            return 15

        ig_audit = audit_instagram_existing_post(meta)
        print()
        print("Ad created: NO")
        print("Spend possible: NO (no Ad; campaign+adset PAUSED)")
        print("Housing note: smoke campaign special_ad_category=NONE; "
              "production real-estate campaigns must use HOUSING separately")
        print(f"Instagram existing-post audit: {ig_audit['status']}")
        for reason in ig_audit["reasons"]:
            print(f"  - {reason}")
        print()
        print("LIVE FACEBOOK EXISTING-POST CREATIVE SMOKE TEST PASSED")
        return 0

    except (MetaApiError, MetaSafetyError) as exc:
        print(f"FAILED: {exc}")
        if "1487194" in str(exc) or "pages_manage_ads" in str(exc):
            print()
            print("BLOCKER (Meta permissions / Page→Ads wiring):")
            print("  Current token scopes include: ads_management, ads_read, pages_read_engagement")
            print("  Missing for existing-post Ad Creative (typical): pages_manage_ads")
            print("  Also verify: Business Manager Page advertiser access + Ads payment method")
            print("  published_posts.promotion_status observed as inactive; ads_posts edge empty")
            print("  No Creative was created. Campaign/Ad Set remain PAUSED. No Ad created.")
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
