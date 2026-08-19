#!/usr/bin/env python3
"""
PostMyPost IG automation readiness (замена ChatPlace воронок).

API PostMyPost не создаёт automations программно — в проекте PostMyPost
должна быть настроена одна automation:
  Триггер: комментарий в Instagram → AI-ответ в Direct с ссылкой на Telegram.

Этот скрипт после публикации IG:
  - проверяет живую ссылку на пост;
  - готовит UTM-ссылку на Telegram для квалификатора;
  - помечает воронку в Notion (колонки chatplace_funnel_* переиспользуются).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from metricool_post_search import object_id_from_page  # noqa: E402
from publish_pipeline import (  # noqa: E402
    get_prop,
    load_config,
    load_dotenv,
    notion_get_page,
    notion_update_fields,
    post_kind_slot_flags,
    published_url_field,
)
from funnel_notion import (  # noqa: E402
    all_chatplace_kinds_done,
    chatplace_done_field,
    chatplace_id_field,
    is_chatplace_kind_done,
    is_live_social_url,
    notion_checkbox_property,
    notion_rich_text,
    require_instagram_post_kind,
)
from sync_post_urls import sync_post_url_to_notion  # noqa: E402
from utm_tracking import append_utm_to_url  # noqa: E402
import notion_fields as nfc  # noqa: E402


def postmypost_automation_config(config: dict[str, Any]) -> dict[str, Any]:
    return (config.get("postmypost") or {}).get("automation") or {}


def automation_enabled(config: dict[str, Any]) -> bool:
    pmp = config.get("postmypost") or {}
    auto = postmypost_automation_config(config)
    return bool(pmp.get("enabled")) and bool(auto.get("enabled", True))


def should_run_postmypost_automation(
    platform: str,
    config: dict[str, Any],
    *,
    upload_video: bool = False,
    mode: str | None = None,
) -> bool:
    if not automation_enabled(config):
        return False
    if platform != "instagram":
        return False
    auto = postmypost_automation_config(config)
    kinds = list(auto.get("instagram_post_kinds", ["carousel", "reel"]))
    if mode == "carousel":
        return "carousel" in kinds
    if mode == "video" or upload_video:
        return "reel" in kinds
    return bool(kinds)


def build_dm_message(template: str, telegram_url: str, object_id: str) -> str:
    return (
        template.replace("{telegram_url}", telegram_url)
        .replace("{object_id}", object_id)
        .strip()
    )


def prepare_postmypost_job(
    page_id: str,
    platform: str,
    config: dict[str, Any],
    *,
    post_kind: str | None = None,
    page: dict[str, Any] | None = None,
    sync_urls: bool = True,
) -> dict[str, Any]:
    page = page or notion_get_page(page_id)
    fields = config["notion"]["fields"]
    auto = postmypost_automation_config(config)

    if platform == "instagram":
        post_kind = require_instagram_post_kind(platform, post_kind)
        if is_chatplace_kind_done(page, config, platform, post_kind):
            return {
                "page_id": page_id,
                "platform": platform,
                "post_kind": post_kind,
                "skipped": True,
                "reason": f"postmypost_funnel_{post_kind}_done",
            }

    telegram_field = config["notion"]["published_url_fields"]["telegram"]
    telegram_url = get_prop(page, telegram_field, "url")
    if not telegram_url:
        raise ValueError("post_url_telegram is empty — publish to Telegram first")

    object_id = object_id_from_page(page, fields) or ""
    upload_video, mode = post_kind_slot_flags(post_kind)

    if sync_urls:
        post_id_field = config["notion"]["fields"].get("metricool_post_id", "metricool_post_id")
        publication_id = get_prop(page, post_id_field, "rich_text")
        sync_post_url_to_notion(
            page_id,
            platform,
            config,
            post_id=publication_id,
            post_kind=post_kind,
            page=page,
        )
        page = notion_get_page(page_id)

    ig_field = published_url_field(platform, config, upload_video=upload_video, mode=mode)
    instagram_post_url = get_prop(page, ig_field, "url") if ig_field else None
    if not is_live_social_url(instagram_post_url, platform):
        raise ValueError(
            f"Live {platform} post URL not ready in {ig_field!r} "
            f"(got {instagram_post_url!r})"
        )

    telegram_url_tracked = append_utm_to_url(
        telegram_url,
        platform,
        object_id,
        config,
    )
    dm_template = str(
        auto.get(
            "dm_message_template",
            "Полная информация об объекте {object_id}: {telegram_url}",
        )
    )
    dm_message = build_dm_message(dm_template, telegram_url_tracked, object_id)

    return {
        "page_id": page_id,
        "platform": platform,
        "post_kind": post_kind,
        "object_id": object_id,
        "instagram_post_url": instagram_post_url,
        "telegram_url": telegram_url_tracked,
        "dm_message": dm_message,
        "automation_mode": "postmypost_project_rules",
        "automation_note": (
            "Воронка обрабатывается automation в PostMyPost (UI). "
            "DM-шаблон и UTM-ссылка сохранены для квалификатора."
        ),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "ready",
    }


def mark_postmypost_success(
    page_id: str,
    config: dict[str, Any],
    *,
    platform: str,
    post_kind: str | None,
    job: dict[str, Any],
) -> None:
    fields = config["notion"]["fields"]
    kind_label = post_kind or platform
    funnel_id = f"postmypost:{job.get('object_id')}:{post_kind or 'ig'}"
    updates: dict[str, Any] = {
        fields.get("agent6_log", nfc.AGENT6_LOG): notion_rich_text(
            f"postmypost automation {kind_label} ready; tg={job.get('telegram_url', '')[:120]}"
        ),
    }
    if platform == "instagram" and post_kind in {"carousel", "reel"}:
        updates[chatplace_done_field(fields, post_kind)] = notion_checkbox_property(True)
        updates[chatplace_id_field(fields, post_kind)] = notion_rich_text(funnel_id)
    notion_update_fields(page_id, updates)

    if platform == "instagram":
        page = notion_get_page(page_id)
        if all_chatplace_kinds_done(page, config, platform):
            notion_update_fields(
                page_id,
                {
                    fields.get("chatplace_funnel_done", nfc.CHATPLACE_FUNNEL_DONE): (
                        notion_checkbox_property(True)
                    ),
                    fields.get("chatplace_funnel_id", nfc.CHATPLACE_FUNNEL_ID): notion_rich_text(
                        funnel_id
                    ),
                },
            )


def setup_postmypost_funnel(
    page_id: str,
    platform: str,
    config: dict[str, Any],
    *,
    post_kind: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not automation_enabled(config):
        return {"skipped": True, "reason": "postmypost_automation_disabled"}

    job = prepare_postmypost_job(page_id, platform, config, post_kind=post_kind)
    if job.get("skipped"):
        return job

    job_dir = ROOT / "data" / "postmypost_jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{post_kind}" if post_kind else ""
    job_path = job_dir / f"{page_id}_{platform}{suffix}.json"
    with job_path.open("w", encoding="utf-8") as f:
        json.dump(job, f, indent=2, ensure_ascii=False)
    job["job_file"] = str(job_path)

    if dry_run:
        job["dry_run"] = True
        return job

    mark_postmypost_success(
        page_id,
        config,
        platform=platform,
        post_kind=post_kind,
        job=job,
    )
    job["ok"] = True
    return job


def main() -> int:
    load_dotenv()
    config = load_config()
    parser = argparse.ArgumentParser(description="PostMyPost IG automation readiness")
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--platform", default="instagram")
    parser.add_argument("--post-kind", choices=["carousel", "reel", "video"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = setup_postmypost_funnel(
        args.page_id,
        args.platform,
        config,
        post_kind=args.post_kind,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") or result.get("skipped") or result.get("dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
