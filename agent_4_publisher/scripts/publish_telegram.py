#!/usr/bin/env python3
"""
Publish object photos to Telegram channel (images only, no video).

Usage:
  python3 publish_telegram.py --page-id PAGE_ID
  python3 publish_telegram.py --page-id PAGE_ID --dry-run
  python3 publish_telegram.py --check-bot
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from publish_pipeline import (  # noqa: E402
    get_prop,
    is_agent6_locked,
    load_config,
    load_dotenv,
    notion_checkbox_property,
    notion_date_property,
    notion_get_page,
    notion_update_fields,
    parse_gallery_image_urls,
    utc_today_iso,
)

from image_selection import sanitize_telegram_caption, select_diverse_images  # noqa: E402
import notion_fields as nfc  # noqa: E402

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
MAX_CAPTION = 1024
MAX_PHOTOS = 10
URL_IN_TEXT = re.compile(r"https?://\S+")


def telegram_caption_field_name(fields: dict[str, str], config: dict[str, Any]) -> str:
    tg_cfg = config.get("telegram", {})
    return (
        fields.get("caption_telegram")
        or tg_cfg.get("caption_field")
        or nfc.DESCRIPTION_TELEGRAM
    )


def read_telegram_caption_raw(page: dict[str, Any], fields: dict[str, str], config: dict[str, Any]) -> tuple[str, str]:
    """Return (field_name, text) — ONLY from Описание для Telegram."""
    field_name = telegram_caption_field_name(fields, config)
    text = get_prop(page, field_name, "rich_text") or ""
    return field_name, text.strip()


def telegram_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Set TELEGRAM_BOT_TOKEN in .env")
    return token


def telegram_channel(config: dict[str, Any]) -> str:
    return (
        os.environ.get("TELEGRAM_CHANNEL")
        or config.get("telegram", {}).get("channel")
        or "@OpenHome_th"
    ).strip()


def tg_request(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = TELEGRAM_API.format(token=telegram_token(), method=method)
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API {method} HTTP {e.code}: {err}") from e

    if not body.get("ok"):
        raise RuntimeError(f"Telegram API {method} failed: {body}")
    return body["result"]


def check_bot() -> dict[str, Any]:
    token = telegram_token()
    url = TELEGRAM_API.format(token=token, method="getMe")
    with urllib.request.urlopen(url, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"getMe failed: {body}")
    return body["result"]


def build_telegram_caption(page: dict[str, Any], fields: dict[str, str], config: dict[str, Any]) -> str:
    tg_cfg = config.get("telegram", {})
    field_name, text = read_telegram_caption_raw(page, fields, config)
    if not text:
        raise ValueError(f"Empty caption — fill {field_name!r} in Notion (Agent 6 uses ONLY this column)")

    parts = [text]
    if tg_cfg.get("append_object_hashtag", False):
        obj_id = get_prop(page, fields.get("object_id", nfc.OBJECT_ID), "rich_text")
        if obj_id:
            tag = obj_id if obj_id.startswith("#") else f"#{obj_id}"
            if tag not in text:
                parts.append(tag)
    phone = tg_cfg.get("append_contact_phone")
    if phone and phone not in text:
        parts.append(f"☎️ {phone}")

    caption = "\n\n".join(p for p in parts if p)
    caption = sanitize_telegram_caption(caption, config)

    # Локация — после sanitize: strip_urls мог бы срезать maps-ссылку из текста
    # описания, а мы хотим её в посте всегда, если есть в Notion.
    if tg_cfg.get("append_google_maps", True):
        maps_url = (get_prop(page, fields.get("google_maps", nfc.GOOGLE_MAPS), "url") or "").strip()
        if maps_url and maps_url not in caption:
            line = f"📍 Локация: {maps_url}"
            room = MAX_CAPTION - len(line) - 2  # место под «\n\n» + ссылку
            if room < 40:
                caption = line[:MAX_CAPTION]
            else:
                if len(caption) > room:
                    caption = caption[: room - 1].rstrip() + "…"
                caption = f"{caption}\n\n{line}"
    if len(caption) > MAX_CAPTION:
        caption = caption[: MAX_CAPTION - 1] + "…"
    return caption


def channel_post_url(channel: str, message_id: int) -> str:
    username = channel.lstrip("@")
    return f"https://t.me/{username}/{message_id}"


def publish_photos(
    image_urls: list[str],
    caption: str,
    channel: str,
    dry_run: bool,
) -> dict[str, Any]:
    if not image_urls:
        raise ValueError("No images to publish")

    urls = image_urls[:MAX_PHOTOS]
    preview = {
        "channel": channel,
        "photos": len(urls),
        "image_urls": urls[:3],
        "caption_preview": caption[:200],
    }
    if dry_run:
        preview["dry_run"] = True
        return preview

    if len(urls) == 1:
        result = tg_request(
            "sendPhoto",
            {"chat_id": channel, "photo": urls[0], "caption": caption},
        )
        message_id = result["message_id"]
    else:
        media = []
        for i, url in enumerate(urls):
            item: dict[str, Any] = {"type": "photo", "media": url}
            if i == 0:
                item["caption"] = caption
            media.append(item)
        messages = tg_request("sendMediaGroup", {"chat_id": channel, "media": media})
        message_id = messages[0]["message_id"]

    post_url = channel_post_url(channel, message_id)
    return {
        "channel": channel,
        "message_id": message_id,
        "post_url": post_url,
        "photos": len(urls),
    }


def chain_metricool_carousel(
    page_id: str,
    config: dict[str, Any],
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Schedule carousel posts to social platforms via Metricool."""
    from publish_pipeline import default_schedule_time, metricool_enabled, publish_one

    if not metricool_enabled(config):
        return [{"skipped": True, "reason": "metricool_disabled"}]

    platforms = config.get("carousel", {}).get("platforms", [])
    scheduled = default_schedule_time()
    results: list[dict[str, Any]] = []

    for platform in platforms:
        entry: dict[str, Any] = {"platform": platform}
        try:
            entry.update(publish_one(page_id, platform, scheduled, dry_run, config))
            entry["ok"] = True
        except Exception as e:
            entry["ok"] = False
            entry["error"] = str(e)
        results.append(entry)
    return results


def publish_telegram_page(
    page_id: str,
    dry_run: bool,
    force: bool,
    *,
    chain_metricool: bool | None = None,
) -> dict[str, Any]:
    config = load_config()
    fields = config["notion"]["fields"]
    tg_cfg = config.get("telegram", {})
    channel = telegram_channel(config)

    page = notion_get_page(page_id)
    url_field = config["notion"]["published_url_fields"]["telegram"]
    existing = get_prop(page, url_field, "url")
    if is_agent6_locked(page, fields) and not force and existing:
        return {
            "page_id": page_id,
            "skipped": True,
            "reason": "agent6_locked",
            "post_url": existing,
        }
    if existing and not force:
        result: dict[str, Any] = {
            "page_id": page_id,
            "skipped": True,
            "reason": "post_url_telegram already set",
            "post_url": existing,
        }
        should_chain = chain_metricool
        if should_chain is None:
            from publish_pipeline import metricool_enabled

            should_chain = metricool_enabled(config) and bool(
                tg_cfg.get("chain_metricool_after_telegram")
                or config.get("agent6", {}).get("chain_metricool_after_telegram")
            )
        if should_chain:
            try:
                result["metricool_carousel"] = chain_metricool_carousel(
                    page_id, config, dry_run=dry_run
                )
            except Exception as e:
                result["metricool_error"] = str(e)
        return result

    photo_field = fields.get("photo", tg_cfg.get("source_field", nfc.PHOTO))
    gallery_url = get_prop(page, photo_field, "url")
    if not gallery_url:
        raise ValueError(f"No photo URL in Notion field {photo_field!r}")

    max_images = int(tg_cfg.get("max_images", MAX_PHOTOS))
    all_urls = parse_gallery_image_urls(gallery_url)
    image_urls, pick_method = select_diverse_images(all_urls, max_images, config, context="telegram")
    if not image_urls:
        raise ValueError(f"No images found at {gallery_url}")

    caption = build_telegram_caption(page, fields, config)
    field_name, raw_caption = read_telegram_caption_raw(page, fields, config)
    urls_in_source = URL_IN_TEXT.findall(raw_caption)

    result = publish_photos(image_urls, caption, channel, dry_run)
    result["page_id"] = page_id
    result["gallery_url"] = gallery_url
    result["gallery_total"] = len(all_urls)
    result["image_pick_method"] = pick_method
    result["image_urls"] = image_urls
    result["caption_source_field"] = field_name
    result["urls_found_in_notion_field"] = urls_in_source
    result["caption_uses_only_notion_field"] = True

    if not dry_run and result.get("post_url"):
        updates: dict[str, Any] = {url_field: {"url": result["post_url"]}}
        notion_update_fields(page_id, updates)
        result["notion_updated"] = list(updates.keys())

    should_chain = chain_metricool
    if should_chain is None:
        from publish_pipeline import metricool_enabled

        should_chain = metricool_enabled(config) and bool(
            tg_cfg.get("chain_metricool_after_telegram")
            or config.get("agent6", {}).get("chain_metricool_after_telegram")
        )

    if should_chain and (result.get("post_url") or dry_run):
        try:
            result["metricool_carousel"] = chain_metricool_carousel(
                page_id, config, dry_run=dry_run
            )
        except Exception as e:
            result["metricool_error"] = str(e)

    return result


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Publish photos to Telegram channel")
    parser.add_argument("--page-id", help="Notion page ID")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Republish even if post_url_telegram exists")
    parser.add_argument(
        "--no-metricool",
        action="store_true",
        help="Skip Metricool carousel after Telegram",
    )
    parser.add_argument(
        "--metricool-only",
        action="store_true",
        help="Only run Metricool carousel (skip Telegram)",
    )
    parser.add_argument("--check-bot", action="store_true", help="Verify TELEGRAM_BOT_TOKEN via getMe")
    args = parser.parse_args()

    if args.check_bot:
        bot = check_bot()
        channel = telegram_channel(load_config())
        print(json.dumps({"bot": bot, "channel": channel}, indent=2, ensure_ascii=False))
        return 0

    if not args.page_id:
        parser.error("--page-id required (or use --check-bot)")

    if not os.environ.get("NOTION_API_KEY"):
        print("Set NOTION_API_KEY in .env", file=sys.stderr)
        return 1

    try:
        if args.metricool_only:
            config = load_config()
            out = {
                "page_id": args.page_id,
                "metricool_carousel": chain_metricool_carousel(
                    args.page_id, config, dry_run=args.dry_run
                ),
            }
        else:
            chain = None if args.no_metricool else True
            out = publish_telegram_page(
                args.page_id,
                args.dry_run,
                args.force,
                chain_metricool=chain if not args.no_metricool else False,
            )
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
