#!/usr/bin/env python3
"""Read-only: list recent Facebook Page posts for OpenHome (no token print).

Usage:
  PYTHONPATH=src python3 scripts/meta_find_facebook_posts.py
  PYTHONPATH=src python3 scripts/meta_find_facebook_posts.py --limit 10
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List Facebook Page posts (GET only)")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    _load_dotenv(_ROOT / ".env")

    from agent10_marketer.adapters.meta_ads import MetaMarketingApiAdapter
    from agent10_marketer.adapters.meta_errors import MetaApiError
    from agent10_marketer.config import load_budget_config, load_meta_config

    meta = load_meta_config()
    adapter = MetaMarketingApiAdapter(meta, budget=load_budget_config(_ROOT / "config"))

    print("=== Meta Facebook Page posts (read-only) ===")
    print(f"Token: {'SET' if meta.token_set else 'MISSING'}")
    try:
        page = adapter.get_page()
        print(f"Page ID:   {page.get('id')}")
        print(f"Page name: {page.get('name')}")
        print(f"Page link: {page.get('link')}")
        print()
        posts = adapter.list_facebook_page_posts(limit=max(1, min(args.limit, 50)))
    except MetaApiError as exc:
        print(f"FAILED: {exc}")
        return 1

    if not posts:
        print("No published posts found.")
        return 0

    print(f"Showing {len(posts)} post(s). Pick object_story_id manually for creative smoke.")
    print("Do NOT invent IDs. Use Meta `id` field as object_story_id after confirmation.")
    print()
    for i, post in enumerate(posts, start=1):
        msg = (post.get("message") or "").replace("\n", " ")
        snippet = msg[:120] + ("…" if len(msg) > 120 else "")
        print(f"[{i}] id / object_story_id candidate: {post.get('id')}")
        print(f"    created_time: {post.get('created_time')}")
        print(f"    permalink:    {post.get('permalink_url')}")
        print(f"    status_type:  {post.get('status_type')}  published={post.get('is_published')}")
        print(f"    message:      {snippet!r}")
        print()

    methods = [e["method"] for e in adapter.request_log]
    if any(m != "GET" for m in methods):
        print(f"FAIL: non-GET methods observed: {methods}")
        return 2
    print(f"Requests: {len(methods)} GET-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
