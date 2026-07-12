#!/usr/bin/env python3
"""
Prepare (and optionally queue) a ChatPlace funnel for an Instagram/TikTok post.

Usage:
  python3 setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram
  python3 setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from metricool_post_search import object_id_from_page  # noqa: E402
from publish_pipeline import (  # noqa: E402
    get_prop,
    instagram_post_kind,
    load_config,
    load_dotenv,
    network_for,
    notion_get_page,
    notion_update_fields,
    published_url_field,
)
from sync_post_urls import sync_post_url_to_notion  # noqa: E402


def notion_checkbox_property(value: bool) -> dict[str, Any]:
    return {"checkbox": value}


def notion_rich_text(content: str) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": content[:2000]}}]}


def chatplace_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("chatplace", {})


def instagram_post_kinds_for_chatplace(config: dict[str, Any]) -> list[str]:
    return list(chatplace_config(config).get("instagram_post_kinds", ["carousel", "reel"]))


def chatplace_done_field(fields: dict[str, Any], post_kind: str) -> str:
    key = f"chatplace_funnel_{post_kind}_done"
    return fields.get(key, key)


def chatplace_id_field(fields: dict[str, Any], post_kind: str) -> str:
    key = f"chatplace_funnel_{post_kind}_id"
    return fields.get(key, key)


def require_instagram_post_kind(platform: str, post_kind: str | None) -> str:
    if platform != "instagram":
        return post_kind or ""
    if post_kind not in {"carousel", "reel"}:
        raise ValueError("post_kind required for instagram: carousel or reel")
    return post_kind


def is_chatplace_kind_done(
    page: dict[str, Any],
    config: dict[str, Any],
    platform: str,
    post_kind: str,
) -> bool:
    fields = config["notion"]["fields"]
    if platform == "instagram":
        if get_prop(page, chatplace_done_field(fields, post_kind), "checkbox"):
            return True
        if post_kind == "carousel" and get_prop(
            page, fields.get("chatplace_funnel_done", "chatplace_funnel_done"), "checkbox"
        ):
            return True
        return False
    return get_prop(page, fields.get("chatplace_funnel_done", "chatplace_funnel_done"), "checkbox")


def all_chatplace_kinds_done(page: dict[str, Any], config: dict[str, Any], platform: str) -> bool:
    if platform != "instagram":
        fields = config["notion"]["fields"]
        return bool(
            get_prop(page, fields.get("chatplace_funnel_done", "chatplace_funnel_done"), "checkbox")
        )
    return all(
        is_chatplace_kind_done(page, config, platform, kind)
        for kind in instagram_post_kinds_for_chatplace(config)
    )


def is_live_social_url(url: str | None, platform: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    lowered = url.lower()
    if "metricool.com" in lowered:
        return False
    hints = {
        "instagram": ("instagram.com",),
        "tiktok": ("tiktok.com",),
    }
    for hint in hints.get(platform, ()):
        if hint in lowered:
            return True
    return False


def should_run_chatplace(
    platform: str,
    config: dict[str, Any],
    *,
    upload_video: bool = False,
    mode: str | None = None,
) -> bool:
    cp = chatplace_config(config)
    if not cp.get("enabled"):
        return False
    if platform not in cp.get("platforms", ["instagram"]):
        return False
    if platform == "instagram":
        kinds = cp.get("instagram_post_kinds", ["carousel"])
        kind = instagram_post_kind(upload_video=upload_video, mode=mode)
        return kind in kinds
    return True


def build_dm_message(template: str, telegram_url: str, object_id: str) -> str:
    return (
        template.replace("{telegram_url}", telegram_url)
        .replace("{object_id}", object_id)
        .replace("{tg_link}", telegram_url)
    )


def build_funnel_name(
    object_id: str,
    config: dict[str, Any],
    *,
    post_kind: str | None = None,
) -> str:
    cp = chatplace_config(config)
    template = str(cp.get("funnel_name_template", "{object_id}"))
    name = (
        template.replace("{object_id}", object_id)
        .replace("{post_kind}", post_kind or "")
        .strip()
    )
    return name or object_id


def build_mcp_prompt(job: dict[str, Any]) -> str:
    post_kind = job.get("post_kind")
    kind_line = f"- Post type: Instagram {post_kind}\n" if post_kind else ""
    return (
        "Create an Instagram comment-to-DM automation in ChatPlace with these exact settings:\n"
        f"{kind_line}"
        f"- Bind to this published post: {job.get('instagram_post_url')}\n"
        f"- Trigger: any comment under this post (commentAnyValue)\n"
        f"- Action: send Instagram DM with this message:\n"
        f"  {job.get('dm_message')}\n"
        f"- Object reference: {job.get('object_id')}\n"
        f"- Telegram link to include: {job.get('telegram_url')}\n"
        f"- Automation name in ChatPlace: {job.get('funnel_name')}\n"
        "Return the created automation ID when done."
    )


def build_agent_prompt(job: dict[str, Any]) -> str:
    return build_mcp_prompt(job)


def prepare_chatplace_job(
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
    cp = chatplace_config(config)

    if platform == "instagram":
        post_kind = require_instagram_post_kind(platform, post_kind)
        if is_chatplace_kind_done(page, config, platform, post_kind):
            return {
                "page_id": page_id,
                "platform": platform,
                "post_kind": post_kind,
                "skipped": True,
                "reason": f"chatplace_funnel_{post_kind}_done",
            }
    elif get_prop(page, fields.get("chatplace_funnel_done", "chatplace_funnel_done"), "checkbox"):
        return {
            "page_id": page_id,
            "platform": platform,
            "skipped": True,
            "reason": "chatplace_funnel_done",
        }

    telegram_url = get_prop(page, config["notion"]["published_url_fields"]["telegram"], "url")
    if not telegram_url:
        raise ValueError("post_url_telegram is empty — publish to Telegram first")

    object_id = object_id_from_page(page, fields) or ""
    cta_field = fields.get("caption_instagram_cta", "CTA Instagram")
    instagram_cta = get_prop(page, cta_field, "rich_text") or cp.get(
        "default_instagram_cta",
        "Оставьте «+» в комментарии — пришлём полную информацию об объекте в Direct.",
    )

    upload_video = post_kind == "reel"
    mode = "video" if post_kind == "reel" else "carousel" if post_kind == "carousel" else None

    if sync_urls:
        sync_post_url_to_notion(
            page_id,
            platform,
            config,
            post_kind=post_kind,
            page=page,
        )
        page = notion_get_page(page_id)

    ig_field = published_url_field(platform, config, upload_video=upload_video, mode=mode)
    instagram_post_url = get_prop(page, ig_field, "url") if ig_field else None
    if not is_live_social_url(instagram_post_url, platform):
        raise ValueError(
            f"Live {platform} post URL not ready in {ig_field!r} "
            f"(got {instagram_post_url!r}) — wait for Metricool publish + URL sync"
        )

    trigger_type = str(cp.get("instagram_comment_trigger", "commentAnyValue"))
    trigger = str(cp.get("trigger_keyword", "+"))
    dm_template = cp.get(
        "dm_message_template",
        "Полная информация об объекте {object_id}: {telegram_url}",
    )
    dm_message = build_dm_message(dm_template, telegram_url, object_id)
    funnel_name = build_funnel_name(object_id, config, post_kind=post_kind or None)

    job = {
        "page_id": page_id,
        "platform": platform,
        "post_kind": post_kind,
        "object_id": object_id,
        "funnel_name": funnel_name,
        "trigger_type": trigger_type,
        "trigger_keyword": trigger,
        "instagram_cta": instagram_cta,
        "telegram_url": telegram_url,
        "instagram_post_url": instagram_post_url,
        "dm_message": dm_message,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mcp_url": cp.get("mcp_url", "https://mcp.chatplace.io/mcp"),
        "status": "pending_mcp",
    }
    job["mcp_prompt"] = build_mcp_prompt(job)
    job["agent_prompt"] = job["mcp_prompt"]
    return job


def write_job_file(job: dict[str, Any], config: dict[str, Any]) -> Path:
    job_dir = ROOT / "data" / "chatplace_jobs"
    job_dir.mkdir(parents=True, exist_ok=True)
    page_id = job["page_id"]
    platform = job["platform"]
    post_kind = job.get("post_kind")
    suffix = f"_{post_kind}" if post_kind else ""
    path = job_dir / f"{page_id}_{platform}{suffix}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(job, f, indent=2, ensure_ascii=False)
    return path


def mark_chatplace_queued(page_id: str, config: dict[str, Any], job_path: Path) -> None:
    fields = config["notion"]["fields"]
    log_field = fields.get("agent6_log", "agent6_log")
    notion_update_fields(
        page_id,
        {
            log_field: {
                "rich_text": [
                    {
                        "text": {
                            "content": f"chatplace job queued: {job_path.name}",
                        }
                    }
                ]
            },
        },
    )


def mark_chatplace_success(
    page_id: str,
    config: dict[str, Any],
    *,
    platform: str,
    post_kind: str | None,
    funnel_id: str | None,
    mcp_response: str,
) -> None:
    fields = config["notion"]["fields"]
    kind_label = post_kind or platform
    updates: dict[str, Any] = {
        fields.get("agent6_log", "agent6_log"): notion_rich_text(
            f"chatplace {kind_label} mcp ok"
            f"{f' id={funnel_id}' if funnel_id else ''}: {mcp_response[:300]}"
        ),
    }
    if platform == "instagram" and post_kind in {"carousel", "reel"}:
        updates[chatplace_done_field(fields, post_kind)] = notion_checkbox_property(True)
        if funnel_id:
            updates[chatplace_id_field(fields, post_kind)] = notion_rich_text(funnel_id)
        if post_kind == "carousel" and funnel_id:
            updates[fields.get("chatplace_funnel_id", "chatplace_funnel_id")] = notion_rich_text(
                funnel_id
            )
    elif funnel_id:
        updates[fields.get("chatplace_funnel_id", "chatplace_funnel_id")] = notion_rich_text(
            funnel_id
        )
        updates[fields.get("chatplace_funnel_done", "chatplace_funnel_done")] = (
            notion_checkbox_property(True)
        )

    notion_update_fields(page_id, updates)

    if platform == "instagram":
        page = notion_get_page(page_id)
        if all_chatplace_kinds_done(page, config, platform):
            notion_update_fields(
                page_id,
                {
                    fields.get("chatplace_funnel_done", "chatplace_funnel_done"): (
                        notion_checkbox_property(True)
                    ),
                },
            )


def execute_chatplace_via_mcp(job: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    from chatplace_mcp import (
        ChatPlaceMcpError,
        create_instagram_comment_funnel,
        find_instagram_bot_id,
        match_media_id_for_post,
        run_funnel_prompt,
    )

    cp = chatplace_config(config)
    if job.get("platform") == "instagram" and cp.get("use_structured_mcp", True):
        bot_id = find_instagram_bot_id(config)
        media_id = match_media_id_for_post(
            bot_id, str(job.get("instagram_post_url") or ""), config
        )
        if not media_id:
            raise ChatPlaceMcpError(
                f"Instagram post not found in ChatPlace media list: {job.get('instagram_post_url')}"
            )
        mcp_result = create_instagram_comment_funnel(
            bot_id=bot_id,
            media_id=media_id,
            trigger_type=str(job.get("trigger_type") or cp.get("instagram_comment_trigger", "commentAnyValue")),
            trigger_keyword=str(job.get("trigger_keyword") or "+") or None,
            telegram_url=str(job.get("telegram_url") or ""),
            dm_message=str(job.get("dm_message") or ""),
            name=str(job.get("funnel_name") or job.get("object_id") or "") or None,
            welcome_message=str(
                cp.get(
                    "welcome_message",
                    "Спасибо! Нажмите кнопку ниже, чтобы получить полную информацию об объекте.",
                )
            ),
            welcome_button=str(cp.get("welcome_button", "Получить информацию")),
            button_text=str(cp.get("link_button_text", "Открыть в Telegram")),
            auto_comment=str(cp.get("auto_comment_reply", "Проверьте Direct 📩")),
            config=config,
        )
        return {
            "execution": "mcp_structured",
            "mcp_tool": mcp_result.get("tool"),
            "mcp_response": mcp_result.get("text"),
            "funnel_id": mcp_result.get("automation_id"),
            "funnel_name": mcp_result.get("automation_name"),
            "bot_id": bot_id,
            "media_id": media_id,
            "raw": mcp_result.get("raw"),
        }

    prompt = job.get("mcp_prompt") or job.get("agent_prompt") or ""
    mcp_result = run_funnel_prompt(prompt, config)
    from chatplace_mcp import extract_funnel_id

    funnel_id = extract_funnel_id(mcp_result.get("text", ""))
    return {
        "execution": "mcp_prompt",
        "mcp_tool": mcp_result.get("tool"),
        "mcp_response": mcp_result.get("text"),
        "funnel_id": funnel_id,
        "raw": mcp_result.get("raw"),
    }


def setup_chatplace_funnel(
    page_id: str,
    platform: str,
    config: dict[str, Any],
    *,
    post_kind: str | None = None,
    dry_run: bool = False,
    via_mcp: bool | None = None,
) -> dict[str, Any]:
    cp = chatplace_config(config)
    use_mcp = cp.get("execution", "mcp") == "mcp" if via_mcp is None else via_mcp

    if use_mcp and not os.environ.get("CHATPLACE_API_KEY") and not dry_run:
        raise ValueError("CHATPLACE_API_KEY is not set in .env — required for ChatPlace MCP")

    job = prepare_chatplace_job(page_id, platform, config, post_kind=post_kind)
    if job.get("skipped"):
        return job

    if dry_run:
        job["dry_run"] = True
        job["execution"] = "mcp" if use_mcp else "job_file"
        return job

    job_path = write_job_file(job, config)
    job["job_file"] = str(job_path)

    if not use_mcp:
        mark_chatplace_queued(page_id, config, job_path)
        job["next_step"] = "Run ChatPlace MCP manually with agent_prompt from job file"
        return job

    try:
        mcp_out = execute_chatplace_via_mcp(job, config)
    except Exception as exc:
        job["status"] = "mcp_failed"
        job["error"] = str(exc)
        job["next_step"] = "Retry with setup_chatplace_funnel.py or run MCP in Cursor chat"
        with job_path.open("w", encoding="utf-8") as f:
            json.dump({**job, "mcp_error": str(exc)}, f, indent=2, ensure_ascii=False)
        raise

    mark_chatplace_success(
        page_id,
        config,
        platform=platform,
        post_kind=post_kind,
        funnel_id=mcp_out.get("funnel_id"),
        mcp_response=str(mcp_out.get("mcp_response") or ""),
    )
    job.update(mcp_out)
    job["status"] = "completed"
    with job_path.open("w", encoding="utf-8") as f:
        json.dump(job, f, indent=2, ensure_ascii=False)
    return job


def main() -> int:
    load_dotenv()
    config = load_config()

    parser = argparse.ArgumentParser(description="Prepare ChatPlace funnel job")
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--platform", default="instagram", choices=["instagram", "tiktok"])
    parser.add_argument(
        "--post-kind",
        choices=["carousel", "reel"],
        help="Instagram: carousel or reel (default: all kinds from config)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--job-only",
        action="store_true",
        help="Only write job JSON, do not call ChatPlace MCP",
    )
    args = parser.parse_args()

    try:
        if args.platform == "instagram" and not args.post_kind:
            results: list[dict[str, Any]] = []
            for kind in instagram_post_kinds_for_chatplace(config):
                results.append(
                    setup_chatplace_funnel(
                        args.page_id,
                        args.platform,
                        config,
                        post_kind=kind,
                        dry_run=args.dry_run,
                        via_mcp=not args.job_only,
                    )
                )
            result = {"page_id": args.page_id, "platform": args.platform, "results": results}
        else:
            result = setup_chatplace_funnel(
                args.page_id,
                args.platform,
                config,
                post_kind=args.post_kind,
                dry_run=args.dry_run,
                via_mcp=not args.job_only,
            )
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    if isinstance(result.get("results"), list):
        if all(item.get("skipped") for item in result["results"]):
            return 2
        return 0 if any(
            item.get("job_file") or item.get("dry_run") or item.get("status") == "completed"
            for item in result["results"]
        ) else 2
    return 0 if not result.get("skipped") else 2


if __name__ == "__main__":
    sys.exit(main())
