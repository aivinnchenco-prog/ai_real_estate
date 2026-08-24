#!/usr/bin/env python3
"""Mark FB groups as already published (resume after partial phone run)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from publisher_social.config import load_fb_groups, load_publisher_config
from publisher_social.dotenv_util import load_dotenv
from publisher_social.state import load_state, mark_fb_group_published, pending_fb_group_urls


def _read_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed fb_groups_published progress in local state")
    parser.add_argument("--object-id", required=True)
    parser.add_argument(
        "--mark-done-from-full-list",
        action="store_true",
        help="Mark groups that are in full list but NOT in resume list as published",
    )
    parser.add_argument("--resume-file", default="config/fb_groups_list_resume.txt")
    parser.add_argument("--full-file", default=None, help="defaults to publisher fb_groups groups_file")
    parser.add_argument("--url", action="append", dest="urls", help="Explicit group URL to mark done")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_publisher_config()
    state = load_state()
    marked: list[str] = []

    if args.urls:
        for url in args.urls:
            mark_fb_group_published(state, args.object_id, url, note="seeded manually")
            marked.append(url)

    if args.mark_done_from_full_list:
        full_rel = args.full_file or (cfg.get("fb_groups") or {}).get(
            "groups_file", "config/fb_groups_list.txt"
        )
        full = _read_urls(ROOT / full_rel)
        resume = _read_urls(ROOT / args.resume_file)
        resume_set = {u.rstrip("/") + "/" for u in resume}
        for url in full:
            key = url.rstrip("/") + "/"
            if key not in resume_set:
                mark_fb_group_published(
                    state,
                    args.object_id,
                    url,
                    note="seeded from resume diff (phone partial)",
                )
                marked.append(url)

    all_groups = load_fb_groups(cfg)
    pending = pending_fb_group_urls(state, args.object_id, all_groups)
    print(f"object_id={args.object_id}")
    print(f"marked={len(marked)}")
    for url in marked:
        print(f"  done: {url}")
    print(f"pending_groups={len(pending)}/{len(all_groups)}")
    for url in pending:
        print(f"  pending: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
