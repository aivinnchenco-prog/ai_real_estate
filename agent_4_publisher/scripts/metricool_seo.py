"""Metricool SEO helpers — hashtags, location, per-platform caption adaptation."""

from __future__ import annotations

import random
import re

import notion_fields as nfc

HASHTAG_RE = re.compile(r"#\w+", re.UNICODE)
WORD_RE = re.compile(r"[^\w]+", re.UNICODE)

PLATFORM_LIMITS: dict[str, int] = {
    "instagram": 2200,
    "tiktok": 2200,
    "facebook": 5000,
    "twitter": 280,
    "x": 280,
    "threads": 500,
    "linkedin": 3000,
    "youtube": 5000,
}


def seo_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("metricool", {}).get("seo", {})


def get_select_prop(page: dict[str, Any], field_name: str | None, get_prop: Callable) -> str | None:
    if not field_name:
        return None
    props = page.get("properties", {})
    prop = props.get(field_name, {})
    if prop.get("type") != "select":
        return None
    sel = prop.get("select") or {}
    return sel.get("name")


def get_place_prop(page: dict[str, Any], field_name: str | None, get_prop: Callable) -> dict[str, Any] | None:
    if not field_name:
        return None
    props = page.get("properties", {})
    prop = props.get(field_name, {})
    if prop.get("type") != "place":
        return None
    place = prop.get("place")
    return place if isinstance(place, dict) else None


def slug_hashtag(value: str) -> str:
    cleaned = WORD_RE.sub("", value)
    if not cleaned:
        return ""
    return f"#{cleaned}"


def normalize_hashtag_block(text: str, max_tags: int) -> str:
    tags: list[str] = []
    seen: set[str] = set()
    for raw in HASHTAG_RE.findall(text):
        tag = raw if raw.startswith("#") else f"#{raw}"
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) >= max_tags:
            break
    return " ".join(tags)


def build_hashtags(page: dict[str, Any], fields: dict[str, str], config: dict[str, Any], get_prop: Callable) -> str:
    seo = seo_config(config)
    max_tags = int(seo.get("max_hashtags", 30))
    rnd = seo.get("hashtag_randomize", {})

    if rnd.get("enabled", True):
        return _build_random_hashtags(page, fields, config, get_prop, max_tags, rnd)

    tags: list[str] = []
    seen: set[str] = set()

    def add(tag: str) -> None:
        if not tag:
            return
        if not tag.startswith("#"):
            tag = f"#{tag}"
        key = tag.lower()
        if key in seen:
            return
        seen.add(key)
        tags.append(tag)

    for tag in seo.get("default_hashtags", []):
        add(str(tag))

    district = get_prop(page, fields.get("district", nfc.DISTRICT), "rich_text")
    if district:
        add(slug_hashtag(district))

    housing = get_select_prop(page, fields.get("housing_type", nfc.HOUSING_TYPE), get_prop)
    if housing:
        add(slug_hashtag(housing))

    obj_id = get_prop(page, fields.get("object_id", nfc.OBJECT_ID), "rich_text")
    if obj_id:
        add(slug_hashtag(obj_id))

    return " ".join(tags[:max_tags])


def _build_random_hashtags(
    page: dict[str, Any],
    fields: dict[str, str],
    config: dict[str, Any],
    get_prop: Callable,
    max_tags: int,
    rnd: dict[str, Any],
) -> str:
    pick_min = int(rnd.get("pick_min", 10))
    pick_max = int(rnd.get("pick_max", 18))
    target = random.randint(pick_min, min(pick_max, max_tags))

    tags: list[str] = []
    seen: set[str] = set()

    def add(tag: str, *, force: bool = False) -> None:
        if not tag:
            return
        if not tag.startswith("#"):
            tag = f"#{tag}"
        key = tag.lower()
        if key in seen:
            return
        seen.add(key)
        tags.append(tag)

    for tag in rnd.get("always_include", ["#TripHomePhuket"]):
        add(str(tag), force=True)

    obj_id = get_prop(page, fields.get("object_id", nfc.OBJECT_ID), "rich_text")
    if obj_id:
        add(slug_hashtag(obj_id), force=True)

    district = get_prop(page, fields.get("district", nfc.DISTRICT), "rich_text")
    if district:
        add(slug_hashtag(district))

    housing = get_select_prop(page, fields.get("housing_type", nfc.HOUSING_TYPE), get_prop)
    if housing:
        add(slug_hashtag(housing))

    view = get_select_prop(page, fields.get("view", "Вид"), get_prop)
    if view:
        add(slug_hashtag(view))

    pool_picks: list[str] = []
    pools: dict[str, list[str]] = rnd.get("pools", {})
    pool_pick = rnd.get("pool_pick", {})
    for pool_name, candidates in pools.items():
        if not candidates:
            continue
        n = int(pool_pick.get(pool_name, 2))
        n = min(n, len(candidates))
        pool_picks.extend(random.sample(list(candidates), n))

    random.shuffle(pool_picks)
    for tag in pool_picks:
        add(tag)
        if len(tags) >= target:
            break

    return " ".join(tags[:max_tags])


def location_query_from_page(page: dict[str, Any], fields: dict[str, str], config: dict[str, Any], get_prop: Callable) -> str:
    seo = seo_config(config)
    loc_cfg = seo.get("location_search", {})
    parts: list[str] = []
    for key in loc_cfg.get("query_fields", ["district", "address"]):
        field_name = fields.get(key)
        if not field_name:
            continue
        val = get_prop(page, field_name, "rich_text")
        if val:
            parts.append(val.strip())
    if parts:
        return ", ".join(parts)
    return str(loc_cfg.get("fallback_query", "Phuket, Thailand"))


def pick_location_match(candidates: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    if not candidates:
        return None
    tokens = [t.lower() for t in re.split(r"[\s,]+", query) if len(t) > 2]
    best: tuple[int, dict[str, Any]] | None = None
    for item in candidates:
        name = (item.get("name") or "").lower()
        score = sum(1 for t in tokens if t in name)
        if best is None or score > best[0]:
            best = (score, item)
    if best and best[0] > 0:
        return best[1]
    return candidates[0]


def metricool_location_from_notion_place(place: dict[str, Any]) -> dict[str, Any] | None:
    lat = place.get("latitude") or place.get("lat")
    lon = place.get("longitude") or place.get("lon")
    name = place.get("name") or ""
    if lat is None or lon is None:
        return None
    return {
        "name": name or "Location",
        "id": place.get("id") or "",
        "link": place.get("url") or "",
        "location": {
            "latitude": float(lat),
            "longitude": float(lon),
            "city": place.get("city") or "",
            "country": place.get("country") or "",
            "state": place.get("state") or "",
            "street": place.get("street") or "",
            "zip": place.get("zip") or "",
        },
    }


def metricool_location_from_facebook_item(item: dict[str, Any]) -> dict[str, Any]:
    loc = item.get("location") or {}
    return {
        "name": item.get("name") or "",
        "id": str(item.get("id") or ""),
        "link": item.get("link") or "",
        "location": {
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "city": loc.get("city") or "",
            "country": loc.get("country") or "",
            "state": loc.get("state") or "",
            "street": loc.get("street") or "",
            "zip": loc.get("zip") or "",
        },
    }


def resolve_metricool_location(
    page: dict[str, Any],
    fields: dict[str, str],
    platform: str,
    config: dict[str, Any],
    get_prop: Callable,
    search_locations: Callable[[str], list[dict[str, Any]]],
) -> dict[str, Any] | None:
    seo = seo_config(config)
    loc_cfg = seo.get("location_search", {})
    if not loc_cfg.get("enabled", True):
        return None
    networks = loc_cfg.get("networks", ["instagram", "facebook"])
    network = platform if platform != "x" else "twitter"
    if network not in networks and platform not in networks:
        return None

    place = get_place_prop(page, fields.get("location", "Локация"), get_prop)
    if place:
        built = metricool_location_from_notion_place(place)
        if built:
            return built

    district = (get_prop(page, fields.get("district", nfc.DISTRICT), "rich_text") or "").strip()
    queries = []
    if district:
        queries.append(f"{district}, Phuket, Thailand")
        queries.append(f"Bang Tao, Phuket, Thailand")
    queries.append(location_query_from_page(page, fields, config, get_prop))

    seen_q: set[str] = set()
    for query in queries:
        if not query or query in seen_q:
            continue
        seen_q.add(query)
        try:
            matches = search_locations(query)
        except Exception:
            matches = []
        picked = pick_location_match(matches, query)
        if picked:
            return metricool_location_from_facebook_item(picked)

    district_l = district.lower()
    for item in loc_cfg.get("fallback_locations", []):
        for token in item.get("district_match", []):
            if token.lower() in district_l or district_l in token.lower():
                loc = item.get("location")
                if isinstance(loc, dict):
                    return loc
    return None


def read_base_caption(
    page: dict[str, Any],
    fields: dict[str, str],
    platform: str,
    config: dict[str, Any],
    get_prop: Callable,
) -> tuple[str, str]:
    """Return (field_name, text) — platform-specific Notion column or fallback."""
    caption_map = config.get("notion", {}).get("caption_by_platform", {})
    caption_key = caption_map.get(platform, "caption_social")
    field_name = fields.get(caption_key) or fields.get("caption_social", nfc.DESCRIPTION_SOCIAL)
    text = get_prop(page, field_name, "rich_text") or ""
    if text.strip():
        return field_name, text.strip()

    fallback = fields.get("caption_social", nfc.DESCRIPTION_SOCIAL)
    if field_name != fallback:
        text = get_prop(page, fallback, "rich_text") or ""
        if text.strip():
            return fallback, text.strip()

    return field_name, ""


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"


def adapt_caption_for_platform(
    base_text: str,
    hashtags: str,
    platform: str,
    config: dict[str, Any],
) -> dict[str, str]:
    seo = seo_config(config)
    limits = {**PLATFORM_LIMITS, **{k: int(v) for k, v in seo.get("platform_limits", {}).items()}}
    limit = limits.get(platform, 2200)

    placement = seo.get("hashtag_placement", {})
    where = placement.get(platform, placement.get("instagram", "first_comment"))

    adapters = seo.get("platform_adapters", {})
    adapter = adapters.get(platform, {})

    text = base_text
    if adapter.get("strip_urls", platform in {"twitter", "x", "threads"}):
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if adapter.get("max_hashtags_in_caption"):
        max_h = int(adapter["max_hashtags_in_caption"])
        hashtags = " ".join(hashtags.split()[:max_h])

    first_comment = ""
    if hashtags and where == "first_comment":
        first_comment = hashtags
        main = truncate_text(text, limit)
    elif hashtags and where == "caption":
        combined = f"{text.rstrip()}\n\n{hashtags}"
        main = truncate_text(combined, limit)
    else:
        main = truncate_text(text, limit)

    return {
        "text": main,
        "firstCommentText": first_comment,
        "hashtag_placement": where,
    }


def build_metricool_caption_bundle(
    page: dict[str, Any],
    fields: dict[str, str],
    platform: str,
    config: dict[str, Any],
    get_prop: Callable,
    search_locations: Callable[[str], list[dict[str, Any]]],
) -> dict[str, Any]:
    field_name, base = read_base_caption(page, fields, platform, config, get_prop)
    if not base:
        raise ValueError(
            f"Empty caption for {platform} — fill {field_name!r} or «Описание соц.сети» in Notion"
        )

    hashtags = build_hashtags(page, fields, config, get_prop)
    if platform == "instagram":
        cta_field = fields.get("caption_instagram_cta", nfc.CTA_INSTAGRAM)
        cta = (get_prop(page, cta_field, "rich_text") or "").strip()
        if not cta:
            cta = (
                config.get("chatplace", {}).get("default_instagram_cta") or ""
            ).strip()
        if cta:
            base = f"{base.rstrip()}\n\n{cta}"
    adapted = adapt_caption_for_platform(base, hashtags, platform, config)
    location = resolve_metricool_location(page, fields, platform, config, get_prop, search_locations)

    return {
        "caption_source_field": field_name,
        "base_caption_preview": base[:120],
        "hashtags": hashtags,
        **adapted,
        "location": location,
    }
