#!/usr/bin/env python3
"""
Цепочка агентов по статусам Notion CRM.

Agent 1 (parser) → session → Agent 2 → ready_for_video
    → Agent 3 (этот скрипт ждёт Notion) → ready_to_post
    → Agent 6 (ждёт видео) → published

Каждый агент запускается ТОЛЬКО когда предыдущий записал нужные данные в CRM.

Usage:
  python3 scripts/chain_runner.py --from-agent 3 --object-id 20260701_001
  python3 scripts/chain_runner.py --from-agent 6 --object-id 20260701_001 --publish instagram
  python3 scripts/chain_runner.py --watch
  python3 scripts/chain_runner.py --once
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from real_estate_handler import NotionCRM  # noqa: E402
from notion_gate import (  # noqa: E402
    agent3_ready,
    agent6_ready,
    fetch_by_object_id,
    fetch_by_status,
)
from event_log import log_event  # noqa: E402
from pipeline_config import apply_env_overrides  # noqa: E402


def load_dotenv() -> None:
    for name in (".env.real-estate", ".env"):
        p = ROOT / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def load_config() -> dict:
    with (ROOT / "config" / "pipeline.json").open(encoding="utf-8") as f:
        cfg = json.load(f)
    return apply_env_overrides(cfg)


def run_agent3(object_id: str) -> int:
    """Агент 3 = Director (Seedance + хук): FFmpeg-монтаж отвязан от пайплайна,
    код остался в scripts/agent3_video.mjs на случай возврата."""
    director = ROOT.parents[2] / "agent_3_director"
    entry = director / "scripts" / "run_from_notion.mjs"
    if not entry.exists():
        print(f"[chain] SKIP Agent 3: not found {entry}", file=sys.stderr)
        return 1
    print(f"\n[chain] Agent 3 (Director/Seedance) → {object_id}")
    proc = subprocess.run(
        ["node", str(entry), "--object-id", object_id],
        cwd=director,
    )
    return proc.returncode


def run_agent6(page_id: str, platform: str, publisher_script: Path) -> int:
    print(f"\n[chain] Agent 6 → {page_id} ({platform})")
    if not publisher_script.exists():
        print(f"[chain] SKIP Agent 6: not found {publisher_script}", file=sys.stderr)
        return 0
    proc = subprocess.run(
        [sys.executable, str(publisher_script), "--page-id", page_id, "--platform", platform],
        cwd=publisher_script.parent,
    )
    return proc.returncode


def resolve_fb_python() -> Path | None:
    """python3.11 с Playwright — venv FB-парсера (там же авторизованный профиль)."""
    p = ROOT.parents[2] / "agent_1_parser" / "fb_parser" / ".venv311" / "bin" / "python"
    return p if p.exists() else None


def run_fb_branch(page_id: str, script_name: str) -> int:
    """FB-ветки Агента 4 (группы / маркетплейс): свои локи (fb_*_locked),
    «Статус» не меняют, поэтому безопасны рядом с Metricool-веткой."""
    publisher_dir = ROOT.parents[2] / "agent_4_publisher"
    script = publisher_dir / "scripts" / script_name
    fb_python = resolve_fb_python()
    if not script.exists():
        print(f"[chain] SKIP {script_name}: not found {script}", file=sys.stderr)
        return 0
    if fb_python is None:
        print(f"[chain] SKIP {script_name}: нет venv FB-парсера (.venv311)", file=sys.stderr)
        return 0
    cmd = [str(fb_python), str(script), "--page-id", page_id]
    # На сервере без дисплея — виртуальный экран (headful палится у FB меньше)
    import shutil
    if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 1440x900x24"] + cmd
    print(f"\n[chain] Agent 4 FB ({script_name}) → {page_id}")
    proc = subprocess.run(cmd, cwd=publisher_dir)
    return proc.returncode


def resolve_publisher_script(cfg: dict) -> Path:
    candidates: list[Path] = []
    rel = cfg.get("chain", {}).get("publisher_script")
    if rel:
        candidates.append((ROOT / rel).resolve())
    monorepo = ROOT.parent.parent
    estate_root = monorepo.parent
    candidates.extend([
        # Канонический Агент 4 — первым; workspaces/publisher — старая копия
        estate_root / "agent_4_publisher/scripts/publish_pipeline.py",
        monorepo / "workspaces/publisher/scripts/publish_pipeline.py",
        Path("/opt/real-estate-publisher/scripts/publish_pipeline.py"),
    ])
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def chain_auto_publish(cfg: dict) -> bool:
    chain = cfg.get("chain", {})
    if chain.get("auto_agent6") is not None:
        return bool(chain.get("auto_agent6"))
    return bool(chain.get("auto_agent4"))


def continue_chain(
    crm: NotionCRM,
    cfg: dict,
    *,
    object_id: str | None = None,
    from_agent: int = 3,
    publish_platforms: list[str] | None = None,
) -> int:
    nf = cfg["notion"]["fields"]
    statuses = cfg["notion"]["statuses"]
    chain = cfg.get("chain", {})
    publisher = resolve_publisher_script(cfg)
    platforms: list[str] = publish_platforms or chain.get("publish_platforms", [])

    exit_code = 0

    if from_agent <= 3:
        listings: list = []
        if object_id:
            one = fetch_by_object_id(crm, object_id, nf)
            if one:
                listings = [one]
        else:
            listings.extend(fetch_by_status(crm, statuses["after_structurize"], nf))
            listings.extend(
                fetch_by_status(crm, statuses.get("video_failed", "video_failed"), nf)
            )

        processed_3: set[str] = set()
        for listing in listings:
            if listing.object_id in processed_3:
                continue
            ready, reason = agent3_ready(listing, statuses)
            if not ready:
                print(f"[chain] Agent 3 skip {listing.object_id}: {reason}")
                continue
            log_event(listing.object_id, "chain", "agent3_start", page_id=listing.page_id)
            code = run_agent3(listing.object_id)
            processed_3.add(listing.object_id)
            if code != 0:
                log_event(listing.object_id, "chain", "agent3_failed", code=code)
                exit_code = code

    fb_branches: list[str] = []
    if chain.get("publish_fb_groups"):
        fb_branches.append("fb_groups_pipeline.py")
    if chain.get("publish_fb_marketplace"):
        fb_branches.append("fb_marketplace_pipeline.py")

    should_run_agent6 = chain_auto_publish(cfg) or from_agent >= 6 or bool(publish_platforms)
    if should_run_agent6 and (platforms or fb_branches):
        listings6: list = []
        if object_id:
            one = fetch_by_object_id(crm, object_id, nf)
            if one:
                listings6 = [one]
        else:
            listings6 = fetch_by_status(crm, statuses["video_done"], nf)

        for listing in listings6:
            ready6, reason6 = agent6_ready(listing, statuses)
            if not ready6:
                print(f"[chain] Agent 6 skip {listing.object_id}: {reason6}")
                continue
            for platform in platforms:
                log_event(listing.object_id, "chain", "agent6_start", platform=platform)
                code6 = run_agent6(listing.page_id, platform, publisher)
                if code6 != 0:
                    exit_code = code6
                    log_event(listing.object_id, "chain", "agent6_failed", platform=platform)
            for script_name in fb_branches:
                branch = script_name.replace("_pipeline.py", "")
                log_event(listing.object_id, "chain", f"{branch}_start", page_id=listing.page_id)
                code_fb = run_fb_branch(listing.page_id, script_name)
                if code_fb != 0:
                    exit_code = code_fb
                    log_event(listing.object_id, "chain", f"{branch}_failed", code=code_fb)

    return exit_code


def watch_loop(crm: NotionCRM, cfg: dict) -> None:
    interval = cfg.get("chain", {}).get("poll_interval_seconds", 30)
    print(f"[chain] Watching Notion CRM every {interval}s (Ctrl+C to stop)")
    while True:
        try:
            continue_chain(crm, cfg, from_agent=3)
        except Exception as exc:
            print(f"[chain] error: {exc}", file=sys.stderr)
        time.sleep(interval)


def normalize_from_agent(value: int) -> int:
    if value == 4:
        print("[chain] --from-agent 4 is deprecated; use --from-agent 6", file=sys.stderr)
        return 6
    return value


def main() -> int:
    load_dotenv()
    cfg = load_config()
    crm = NotionCRM(
        os.environ["NOTION_API_KEY"],
        os.environ.get("NOTION_DB_ID") or os.environ["NOTION_DATABASE_ID"],
    )

    parser = argparse.ArgumentParser(description="Agent chain orchestrator (Notion-gated)")
    parser.add_argument("--watch", action="store_true", help="Poll Notion and run agents")
    parser.add_argument("--once", action="store_true", help="Process all pending once")
    parser.add_argument("--from-agent", type=int, default=3, choices=[3, 4, 6])
    parser.add_argument("--object-id", help="Single object to process")
    parser.add_argument("--publish", help="Comma platforms for Agent 6 this run (e.g. instagram,tiktok)")
    args = parser.parse_args()

    platforms_override = None
    if args.publish:
        platforms_override = [p.strip() for p in args.publish.split(",") if p.strip()]

    if args.watch:
        watch_loop(crm, cfg)
        return 0

    return continue_chain(
        crm,
        cfg,
        object_id=args.object_id,
        from_agent=normalize_from_agent(args.from_agent),
        publish_platforms=platforms_override,
    )


if __name__ == "__main__":
    sys.exit(main())
