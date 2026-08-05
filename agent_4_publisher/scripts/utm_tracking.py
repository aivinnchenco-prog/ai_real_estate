"""UTM-метки для ссылок в постах — идентификация объекта агентом-квалификатором."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

URL_RE = re.compile(r"https?://[^\s<>\"']+")
UTM_CAMPAIGN_RE = re.compile(
    r"[?&]utm_campaign=([A-Za-z]{0,3}_?\d{8}_\d{3})",
    re.IGNORECASE,
)


def utm_params(platform: str, object_id: str, config: dict[str, Any] | None = None) -> dict[str, str]:
    cfg = (config or {}).get("postmypost", {}).get("utm", {})
    campaign = object_id or ""
    return {
        "utm_source": str(cfg.get("source_template", "{platform}")).format(platform=platform),
        "utm_medium": str(cfg.get("medium", "social")),
        "utm_campaign": campaign,
    }


def append_utm_to_url(url: str, platform: str, object_id: str, config: dict[str, Any] | None = None) -> str:
    if not url or not url.startswith("http"):
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in utm_params(platform, object_id, config).items():
        if value:
            query[key] = [value]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def append_utm_to_urls_in_text(
    text: str,
    platform: str,
    object_id: str,
    config: dict[str, Any] | None = None,
) -> str:
    if not text or not object_id:
        return text

    def repl(match: re.Match[str]) -> str:
        return append_utm_to_url(match.group(0), platform, object_id, config)

    return URL_RE.sub(repl, text)


def extract_utm_campaign(text: str) -> str | None:
    if not text:
        return None
    match = UTM_CAMPAIGN_RE.search(text)
    if not match:
        return None
    return match.group(1).strip()
