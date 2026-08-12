"""Notion adapter — reads existing schema fields only. Does not mutate schema."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from agent10_marketer.models import (
    ContentFormat,
    ObjectSummary,
    PeerGroupAttributes,
    Platform,
    PublicationRecord,
)

# Exact names from schema/notion_schema.json (audit 2026-08).
FIELD_OBJECT_ID = "Объект ID"
FIELD_TITLE = "Название объекта"
FIELD_PROPERTY_TYPE = "Тип жилья"
FIELD_RENT_TYPE = "Тип аренды"
FIELD_BEDROOMS = "Количество комнат"
FIELD_DISTRICT = "Район"
FIELD_PRICE_MONTH = "Цена за месяц"
FIELD_PUBLISHED_AT = "Дата и время публикации"
FIELD_IG_REEL = "post_url_instagram_reel"
FIELD_IG_CAROUSEL = "post_url_instagram_carousel"
FIELD_FB = "post_url_facebook"
# Present in schema but not required for V1 ranking:
FIELD_METRICOOL_POST_ID = "metricool_post_id"
FIELD_METRICOOL_GROUP_ID = "metricool_post_group_id"

# Fields that do NOT exist today (proposal only — never auto-create):
PROPOSED_MISSING_FIELDS = [
    "postmypost_post_id",
    "instagram_media_id",
    "facebook_post_id",
    "publication_format",
    "analytics_reach",
    "analytics_impressions",
    "analytics_views",
    "analytics_likes",
    "analytics_comments",
    "analytics_saves",
    "analytics_shares",
]

NOTION_VERSION = "2022-06-28"


def _rt(prop: dict | None) -> str | None:
    if not prop:
        return None
    parts = prop.get("rich_text") or prop.get("title") or []
    text = "".join(p.get("plain_text", "") for p in parts).strip()
    return text or None


def _url(prop: dict | None) -> str | None:
    if not prop:
        return None
    return prop.get("url") or None


def _select(prop: dict | None) -> str | None:
    if not prop:
        return None
    sel = prop.get("select")
    if not sel:
        return None
    return sel.get("name")


def _number(prop: dict | None) -> float | None:
    if not prop:
        return None
    val = prop.get("number")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _date(prop: dict | None) -> datetime | None:
    if not prop:
        return None
    date = prop.get("date") or {}
    start = date.get("start")
    if not start:
        return None
    try:
        return datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return None


def price_band(price: float | None) -> str | None:
    if price is None:
        return None
    if price < 30000:
        return "lt_30k"
    if price < 60000:
        return "30k_60k"
    if price < 100000:
        return "60k_100k"
    if price < 200000:
        return "100k_200k"
    return "gte_200k"


def parse_object_page(page: dict[str, Any]) -> ObjectSummary:
    props = page.get("properties") or {}
    present = list(props.keys())
    missing: list[str] = []

    object_id = _rt(props.get(FIELD_OBJECT_ID))
    if not object_id:
        missing.append(FIELD_OBJECT_ID)

    price = _number(props.get(FIELD_PRICE_MONTH))
    peer = PeerGroupAttributes(
        property_type=_select(props.get(FIELD_PROPERTY_TYPE)),
        district=_rt(props.get(FIELD_DISTRICT)),
        rent_type=_select(props.get(FIELD_RENT_TYPE)),
        price_band=price_band(price),
        bedrooms=_number(props.get(FIELD_BEDROOMS)),
    )
    for label, value in [
        (FIELD_PROPERTY_TYPE, peer.property_type),
        (FIELD_DISTRICT, peer.district),
        (FIELD_RENT_TYPE, peer.rent_type),
        (FIELD_PRICE_MONTH, price),
        (FIELD_BEDROOMS, peer.bedrooms),
    ]:
        if value is None and label not in missing:
            # Optional for V1 — record as soft-missing, not fatal.
            missing.append(f"optional:{label}")

    return ObjectSummary(
        object_id=object_id or "",
        page_id=page.get("id"),
        title=_rt(props.get(FIELD_TITLE)),
        peer=peer,
        missing_fields=missing,
        raw_properties_present=present,
    )


def publications_from_notion_page(
    page: dict[str, Any],
    *,
    object_id: str,
) -> list[PublicationRecord]:
    """Map existing Notion URL fields into PublicationRecord list (no invented IDs beyond field hash)."""
    props = page.get("properties") or {}
    published_at = _date(props.get(FIELD_PUBLISHED_AT))
    out: list[PublicationRecord] = []

    reel_url = _url(props.get(FIELD_IG_REEL))
    if reel_url:
        out.append(
            PublicationRecord(
                publication_id=f"notion:{object_id}:instagram:reel",
                object_id=object_id,
                platform=Platform.INSTAGRAM,
                format=ContentFormat.REEL,
                instagram_permalink=reel_url,
                permalink=reel_url,
                published_at=published_at,
                status="published",
                source="notion",
                notion_field=FIELD_IG_REEL,
                postmypost_post_id=None,
                instagram_media_id=None,
            )
        )

    carousel_url = _url(props.get(FIELD_IG_CAROUSEL))
    if carousel_url:
        out.append(
            PublicationRecord(
                publication_id=f"notion:{object_id}:instagram:carousel",
                object_id=object_id,
                platform=Platform.INSTAGRAM,
                format=ContentFormat.CAROUSEL,
                instagram_permalink=carousel_url,
                permalink=carousel_url,
                published_at=published_at,
                status="published",
                source="notion",
                notion_field=FIELD_IG_CAROUSEL,
            )
        )

    fb_url = _url(props.get(FIELD_FB))
    if fb_url:
        out.append(
            PublicationRecord(
                publication_id=f"notion:{object_id}:facebook:post",
                object_id=object_id,
                platform=Platform.FACEBOOK,
                format=ContentFormat.POST,
                permalink=fb_url,
                facebook_post_id=None,
                published_at=published_at,
                status="published",
                source="notion",
                notion_field=FIELD_FB,
            )
        )

    return out


class NotionObjectStore(ABC):
    @abstractmethod
    def get_object(self, object_id: str) -> ObjectSummary | None:
        raise NotImplementedError

    @abstractmethod
    def get_publications(self, object_id: str) -> list[PublicationRecord]:
        raise NotImplementedError


class NotionAdapter(NotionObjectStore):
    def __init__(self, *, token: str, database_id: str):
        self.token = token
        self.database_id = database_id
        self.base = "https://api.notion.com/v1"

    @property
    def configured(self) -> bool:
        return bool(self.token and self.database_id)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        url = f"{self.base}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            err = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Notion HTTP {exc.code}: {err}") from exc

    def get_object(self, object_id: str) -> ObjectSummary | None:
        if not self.configured:
            return None
        page = self._find_page(object_id)
        if page is None:
            return None
        return parse_object_page(page)

    def get_publications(self, object_id: str) -> list[PublicationRecord]:
        if not self.configured:
            return []
        page = self._find_page(object_id)
        if page is None:
            return []
        return publications_from_notion_page(page, object_id=object_id)

    def _find_page(self, object_id: str) -> dict[str, Any] | None:
        # Notion rich_text filter is limited; query and match locally for reliability.
        payload = {"page_size": 100}
        path = f"/databases/{self.database_id}/query"
        data = self._request("POST", path, payload)
        target = str(object_id).strip()
        for page in data.get("results") or []:
            summary = parse_object_page(page)
            if summary.object_id == target:
                return page
            # Also accept trailing/leading whitespace variants already stripped.
            if re.fullmatch(re.escape(target), summary.object_id or ""):
                return page
        # Follow one cursor page if needed
        while data.get("has_more") and data.get("next_cursor"):
            payload = {"page_size": 100, "start_cursor": data["next_cursor"]}
            data = self._request("POST", path, payload)
            for page in data.get("results") or []:
                summary = parse_object_page(page)
                if summary.object_id == target:
                    return page
        return None


class MockNotionAdapter(NotionObjectStore):
    """In-memory Notion stand-in for tests / offline CLI."""

    def __init__(
        self,
        objects: dict[str, ObjectSummary] | None = None,
        publications: dict[str, list[PublicationRecord]] | None = None,
    ):
        self.objects = objects or {}
        self.publications = publications or {}

    def get_object(self, object_id: str) -> ObjectSummary | None:
        return self.objects.get(str(object_id))

    def get_publications(self, object_id: str) -> list[PublicationRecord]:
        return list(self.publications.get(str(object_id), []))
