"""Shared Notion funnel status helpers (legacy chatplace_funnel_* column names)."""

from __future__ import annotations

from typing import Any

import notion_fields as nfc
from publish_pipeline import get_prop


def notion_checkbox_property(value: bool) -> dict[str, Any]:
    return {"checkbox": value}


def notion_rich_text(content: str) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": content[:2000]}}]}


def instagram_post_kinds_for_funnel(config: dict[str, Any]) -> list[str]:
    auto = (config.get("postmypost") or {}).get("automation") or {}
    return list(auto.get("instagram_post_kinds", ["carousel", "reel"]))


def funnel_done_field(fields: dict[str, Any], post_kind: str) -> str:
    key = f"chatplace_funnel_{post_kind}_done"
    return fields.get(key, key)


def funnel_id_field(fields: dict[str, Any], post_kind: str) -> str:
    key = f"chatplace_funnel_{post_kind}_id"
    return fields.get(key, key)


# Back-compat aliases for existing imports
chatplace_done_field = funnel_done_field
chatplace_id_field = funnel_id_field
instagram_post_kinds_for_chatplace = instagram_post_kinds_for_funnel


def require_instagram_post_kind(platform: str, post_kind: str | None) -> str:
    if platform != "instagram":
        return post_kind or ""
    if post_kind not in {"carousel", "reel"}:
        raise ValueError("post_kind required for instagram: carousel or reel")
    return post_kind


def is_funnel_kind_done(
    page: dict[str, Any],
    config: dict[str, Any],
    platform: str,
    post_kind: str,
) -> bool:
    fields = config["notion"]["fields"]
    if platform == "instagram":
        if get_prop(page, funnel_done_field(fields, post_kind), "checkbox"):
            return True
        if post_kind == "carousel" and get_prop(
            page, fields.get("chatplace_funnel_done", nfc.CHATPLACE_FUNNEL_DONE), "checkbox"
        ):
            return True
        return False
    return get_prop(page, fields.get("chatplace_funnel_done", nfc.CHATPLACE_FUNNEL_DONE), "checkbox")


is_chatplace_kind_done = is_funnel_kind_done


def all_funnel_kinds_done(page: dict[str, Any], config: dict[str, Any], platform: str) -> bool:
    if platform != "instagram":
        fields = config["notion"]["fields"]
        return bool(
            get_prop(page, fields.get("chatplace_funnel_done", nfc.CHATPLACE_FUNNEL_DONE), "checkbox")
        )
    return all(
        is_funnel_kind_done(page, config, platform, kind)
        for kind in instagram_post_kinds_for_funnel(config)
    )


all_chatplace_kinds_done = all_funnel_kinds_done


def is_live_social_url(url: str | None, platform: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    lowered = url.lower()
    if "metricool.com" in lowered or "postmypost.io" in lowered:
        return False
    hints = {
        "instagram": ("instagram.com",),
        "tiktok": ("tiktok.com",),
    }
    for hint in hints.get(platform, ()):
        if hint in lowered:
            return True
    return False
