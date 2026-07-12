"""Diverse image selection for carousel / Telegram albums."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


def spread_select_urls(urls: list[str], max_count: int) -> list[str]:
    """Evenly spread picks across the gallery (better than first N)."""
    if not urls or max_count <= 0:
        return []
    if len(urls) <= max_count:
        return list(urls)
    if max_count == 1:
        return [urls[0]]

    step = (len(urls) - 1) / (max_count - 1)
    picked: list[str] = []
    seen: set[str] = set()
    for i in range(max_count):
        url = urls[round(i * step)]
        if url not in seen:
            seen.add(url)
            picked.append(url)
    for url in urls:
        if len(picked) >= max_count:
            break
        if url not in seen:
            seen.add(url)
            picked.append(url)
    return picked[:max_count]


def curator_select_urls(
    urls: list[str],
    max_count: int,
    *,
    max_per_category: int = 1,
    curator_url: str,
    timeout: int = 180,
) -> list[str] | None:
    """CLIP curator /select-diverse — one image per room type when available."""
    if not urls or max_count <= 0:
        return []

    by_key = {url.rsplit("/", 1)[-1]: url for url in urls}
    payload = {
        "images": [{"key": key, "url": url} for key, url in by_key.items()],
        "top_k": max_count,
        "max_per_category": max_per_category,
        "max_similarity": 0.88,
        "mmr_lambda": 0.6,
    }
    req = urllib.request.Request(
        f"{curator_url.rstrip('/')}/select-diverse",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None

    selected: list[str] = []
    for item in data.get("selected", []):
        key = item.get("key", "")
        url = by_key.get(key) or item.get("url")
        if url and url not in selected:
            selected.append(url)
    return selected if selected else None


def select_diverse_images(
    all_urls: list[str],
    max_count: int,
    config: dict[str, Any],
    *,
    context: str = "telegram",
) -> tuple[list[str], str]:
    """
    Pick up to max_count diverse images.
    Returns (urls, method) where method is curator|spread|all.
    """
    if not all_urls:
        return [], "none"
    if len(all_urls) <= max_count:
        return list(all_urls), "all"

    tg_cfg = config.get("telegram", {})
    carousel_cfg = config.get("carousel", {})
    diverse = tg_cfg.get("diverse_images", True) if context == "telegram" else carousel_cfg.get(
        "diverse_images", True
    )
    if not diverse:
        return all_urls[:max_count], "sequential"

    curator_url = os.environ.get("CURATOR_URL") or tg_cfg.get("curator_url") or carousel_cfg.get(
        "curator_url"
    )
    max_per_cat = int(
        tg_cfg.get("max_per_category", 1)
        if context == "telegram"
        else carousel_cfg.get("max_per_category", 1)
    )

    if curator_url:
        curated = curator_select_urls(
            all_urls,
            max_count,
            max_per_category=max_per_cat,
            curator_url=curator_url,
        )
        if curated:
            return curated[:max_count], "curator"

    return spread_select_urls(all_urls, max_count), "spread"


def sanitize_telegram_caption(text: str, config: dict[str, Any]) -> str:
    """Remove source listing URLs from TG post (they stay in Notion)."""
    tg_cfg = config.get("telegram", {})
    if not tg_cfg.get("strip_urls", True):
        return text

    patterns = [p.lower() for p in tg_cfg.get("strip_url_patterns", [])]
    url_line = re.compile(r"^\s*https?://\S+\s*$")
    inline_url = re.compile(r"https?://\S+")

    lines_out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if url_line.match(stripped) or (stripped.startswith("http") and " " not in stripped):
            low = stripped.lower()
            if not patterns or any(p in low for p in patterns):
                continue
        cleaned = inline_url.sub("", line).strip()
        if cleaned:
            lines_out.append(cleaned)

    result = re.sub(r"\n{3,}", "\n\n", "\n".join(lines_out)).strip()
    return result
