"""PostMyPost SEO: хештеги, гео, UTM, адаптация текста под каждую соцсеть."""

from __future__ import annotations

import re
from typing import Any, Callable

import notion_fields as nfc
from metricool_seo import (
    adapt_caption_for_platform,
    build_hashtags,
    read_base_caption,
    resolve_metricool_location,
)
from post_reply_agent import (
    agent_context,
    format_agent_template,
    object_code_line,
    reply_agent_config,
    resolve_reply_text,
)
from utm_tracking import append_utm_to_urls_in_text


def postmypost_seo_config(config: dict[str, Any]) -> dict[str, Any]:
    pmp = config.get("postmypost", {})
    base = dict(config.get("metricool", {}).get("seo", {}))
    overrides = pmp.get("seo") or {}
    merged = {**base, **overrides}
    if pmp.get("auto_hashtags", True):
        rnd = dict(merged.get("hashtag_randomize") or {})
        rnd["enabled"] = True
        merged["hashtag_randomize"] = rnd
    return merged


def _seo_config_view(config: dict[str, Any]) -> dict[str, Any]:
    """metricool_seo читает metricool.seo — подставляем merged seo."""
    view = dict(config)
    metricool = dict(view.get("metricool") or {})
    metricool = {**metricool, "seo": postmypost_seo_config(config)}
    view["metricool"] = metricool
    return view


def ai_adapt_caption(text: str, platform: str, config: dict[str, Any]) -> str:
    """Платформенная адаптация текста (аналог PostMyPost AI для публикаций через API)."""
    ai_cfg = (config.get("postmypost") or {}).get("ai_adapt") or {}
    if not ai_cfg.get("enabled", True):
        return text

    network = platform
    if network in {"x", "twitter"}:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        short = " ".join(lines[:3])
        return short[:280]

    if network == "linkedin":
        if not text.lstrip().startswith("🏡"):
            return f"🏡 {text.lstrip()}"

    if network == "tiktok":
        lines = text.splitlines()
        body = "\n".join(lines[:6])
        return body[:2200]

    if network == "threads":
        return text[:500]

    if network == "youtube":
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[:8])[:5000]

    return text


def append_geolocation_line(
    text: str,
    location: dict[str, Any] | None,
    *,
    enabled: bool,
) -> str:
    if not enabled or not location:
        return text
    name = (location.get("name") or "").strip()
    if not name or name in text:
        return text
    return f"{text.rstrip()}\n\n📍 {name}"


def comment_cta_config(config: dict[str, Any]) -> dict[str, Any]:
    pmp = config.get("postmypost") or {}
    return pmp.get("comment_cta") or {}


def comment_cta_enabled(config: dict[str, Any]) -> bool:
    return bool(comment_cta_config(config).get("enabled", True))


def resolve_comment_cta(
    platform: str,
    page: dict[str, Any],
    fields: dict[str, str],
    config: dict[str, Any],
    get_prop: Callable,
    *,
    object_id: str = "",
) -> str:
    """CTA с кодом объекта — для подписи и first comment."""
    if not comment_cta_enabled(config):
        return ""

    cta_field = fields.get("caption_instagram_cta", nfc.CTA_INSTAGRAM)
    from_notion = (get_prop(page, cta_field, "rich_text") or "").strip()
    if from_notion:
        return format_agent_template(from_notion, agent_context(config, object_id=object_id))

    cfg = comment_cta_config(config)
    by_platform = cfg.get("by_platform") or {}
    network = platform if platform not in {"x", "twitter"} else "x"
    raw = str(by_platform.get(platform) or by_platform.get(network) or "").strip()
    if raw:
        return format_agent_template(raw, agent_context(config, object_id=object_id))

    fallback = (
        "Код объекта: {object_id}. Отправьте его менеджеру в Telegram {telegram_channel}"
        "{whatsapp_suffix} — поможем с подбором и оформлением."
    )
    text = resolve_reply_text("caption_cta_template", platform, config, object_id=object_id, fallback=fallback)
    if text:
        return text

    ra_platform = (reply_agent_config(config).get("by_platform") or {}).get(platform) or (
        (reply_agent_config(config).get("by_platform") or {}).get(network)
    )
    if ra_platform:
        return format_agent_template(str(ra_platform), agent_context(config, object_id=object_id))

    channel = (config.get("telegram") or {}).get("channel", "@OpenHome_th")
    if object_id:
        return (
            f"Код объекта: {object_id}. Отправьте его менеджеру в Telegram {channel} "
            "— поможем с подбором и оформлением."
        )
    return f"Смотрите больше вариантов в Telegram {channel} — ссылка в описании профиля."


def append_cta_line(text: str, cta: str) -> str:
    if not cta or cta in text:
        return text
    return f"{text.rstrip()}\n\n{cta}"


def strip_inline_object_tag(text: str, object_id: str) -> str:
    """Убрать «Объект №ID» из тела — код уже идёт отдельной строкой 🏷."""
    if not text or not object_id:
        return text
    patterns = [
        rf"\s*Объект\s*№\s*{re.escape(object_id)}\b",
        rf"\s*Объект\s*#\s*{re.escape(object_id)}\b",
        rf"\s*Object\s*(?:No\.?|#|№)?\s*{re.escape(object_id)}\b",
    ]
    out = text
    for pattern in patterns:
        out = re.sub(pattern, "", out, flags=re.IGNORECASE)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" *\n", "\n", out)
    return out.strip(" \t")


def merge_first_comment(*parts: str) -> str:
    lines = [p.strip() for p in parts if p and p.strip()]
    return "\n\n".join(lines)


def build_postmypost_caption_bundle(
    page: dict[str, Any],
    fields: dict[str, str],
    platform: str,
    config: dict[str, Any],
    get_prop: Callable,
    search_locations: Callable[[str], list[dict[str, Any]]],
    *,
    object_id: str = "",
) -> dict[str, Any]:
    cfg = _seo_config_view(config)
    pmp = config.get("postmypost", {})

    field_name, base = read_base_caption(page, fields, platform, cfg, get_prop)
    if not base:
        raise ValueError(
            f"Empty caption for {platform} — fill {field_name!r} or «Описание соц.сети» in Notion"
        )

    if object_id:
        base = append_utm_to_urls_in_text(base, platform, object_id, config)
        base = strip_inline_object_tag(base, object_id)

    hashtags = build_hashtags(page, fields, cfg, get_prop)
    if object_id:
        # Keep readable id in hashtag (#A_20260810_003), not the underscore-stripped form.
        tag = f"#{object_id}"
        if tag.lower() not in hashtags.lower():
            compact = f"#{object_id.replace('_', '')}"
            if compact.lower() not in hashtags.lower():
                hashtags = f"{hashtags} {tag}".strip()

    code_line = object_code_line(object_id, config)
    if code_line:
        base = append_cta_line(base, code_line)

    cta = resolve_comment_cta(platform, page, fields, config, get_prop, object_id=object_id)
    cta_cfg = comment_cta_config(config)
    if cta and cta_cfg.get("append_to_caption", True):
        base = append_cta_line(base, cta)

    adapted = adapt_caption_for_platform(base, hashtags, platform, cfg)
    location = None
    if pmp.get("auto_geolocation", True):
        location = resolve_metricool_location(
            page, fields, platform, cfg, get_prop, search_locations
        )

    text = adapted["text"]
    text = append_geolocation_line(
        text,
        location,
        enabled=bool(pmp.get("auto_geolocation", True)),
    )
    text = ai_adapt_caption(text, platform, config)

    first_comment = adapted.get("firstCommentText") or ""
    # Missing key → Instagram (legacy default). Explicit [] disables CTA in comments.
    first_comment_platforms = set(
        cta_cfg["append_to_first_comment"]
        if "append_to_first_comment" in cta_cfg
        else ["instagram"]
    )
    if cta and platform in first_comment_platforms:
        first_comment = merge_first_comment(first_comment, cta)
    if first_comment and object_id:
        first_comment = append_utm_to_urls_in_text(first_comment, platform, object_id, config)

    return {
        "caption_source_field": field_name,
        "base_caption_preview": base[:120],
        "hashtags": hashtags,
        "text": text,
        "firstCommentText": first_comment,
        "hashtag_placement": adapted.get("hashtag_placement"),
        "location": location,
    }
