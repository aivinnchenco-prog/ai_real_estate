#!/usr/bin/env python3
"""Show exactly what Notion field Agent 6 uses for Telegram caption."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from publish_telegram import (  # noqa: E402
    build_telegram_caption,
    read_telegram_caption_raw,
    telegram_caption_field_name,
)
from publish_pipeline import get_prop, load_config, load_dotenv, notion_get_page  # noqa: E402
import notion_fields as nfc  # noqa: E402

URL_IN_TEXT = re.compile(r"https?://\S+")


def main() -> int:
    import argparse

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-id", required=True)
    args = parser.parse_args()

    config = load_config()
    fields = config["notion"]["fields"]
    page = notion_get_page(args.page_id)
    field_name = telegram_caption_field_name(fields, config)
    raw_field, raw_text = read_telegram_caption_raw(page, fields, config)
    final = build_telegram_caption(page, fields, config)

    other_fields = {}
    for name in (nfc.DESCRIPTION, nfc.SOURCE_URL, nfc.DESCRIPTION_FB_MARKETPLACE):
        if name == field_name:
            continue
        prop = page.get("properties", {}).get(name, {})
        if prop.get("type") == "rich_text":
            other_fields[name] = "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
        elif prop.get("type") == "url":
            other_fields[name] = prop.get("url") or ""

    report = {
        "page_id": args.page_id,
        "object_id": get_prop(page, fields.get("object_id", nfc.OBJECT_ID), "rich_text"),
        "caption_source_field": field_name,
        "agent6_reads_only_this_field": field_name == raw_field,
        "urls_in_caption_source_field": URL_IN_TEXT.findall(raw_text),
        "urls_in_final_caption_after_sanitize": URL_IN_TEXT.findall(final),
        "raw_caption_preview": raw_text[:400],
        "final_caption_preview": final[:400],
        "other_fields_not_used_for_tg": {
            k: (URL_IN_TEXT.findall(v) if v else [])
            for k, v in other_fields.items()
        },
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
