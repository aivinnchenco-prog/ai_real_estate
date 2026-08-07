"""Notion adapter for Agent 9 (no imports from Agents 1-8)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import requests

# Schema field names (verified against schema/notion_schema.json)
FIELD_OBJECT_ID = "Объект ID"
FIELD_HOUSING_TYPE = "Тип жилья"
FIELD_SOURCE_URL = "Источник объявления"
FIELD_FB_MARKETPLACE_URL = "post_url_FB_marketplace"
FIELD_WHATSAPP = "WhatsApp контакт"
FIELD_OWNER_AGENT = "Владелец / Агент"
FIELD_FB_OUTREACH_STATUS = "FB Outreach Status"
FIELD_FB_OUTREACH_UPDATED = "FB Outreach Updated At"
FIELD_FB_THREAD = "FB Messenger Thread"
FIELD_FB_OUTREACH_ERROR = "FB Outreach Error"

TERMINAL_STATUSES = frozenset({"complete", "declined", "manual_review", "failed"})


@dataclass
class NotionListing:
    page_id: str
    object_id: str
    property_type: str
    facebook_url: str
    listing_id: str
    outreach_status: str
    whatsapp_existing: str
    owner_agent_existing: str


class NotionClient:
    def __init__(self, *, token: str | None = None, database_id: str | None = None):
        self.token = token or os.getenv("AGENT9_NOTION_TOKEN", "")
        self.database_id = database_id or os.getenv("AGENT9_NOTION_DATABASE_ID", "")
        self.base = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    @property
    def configured(self) -> bool:
        return bool(self.token and self.database_id)

    def _req(self, method: str, path: str, **kwargs) -> dict:
        r = requests.request(method, f"{self.base}{path}", headers=self.headers, timeout=30, **kwargs)
        r.raise_for_status()
        return r.json() if r.text else {}

    def query_eligible(self) -> list[NotionListing]:
        if not self.configured:
            return []
        data = self._req("POST", f"/databases/{self.database_id}/query", json={"page_size": 100})
        results = []
        for page in data.get("results", []):
            listing = parse_page(page)
            if listing and is_eligible(listing):
                results.append(listing)
        return results

    def update_outreach(
        self,
        page_id: str,
        *,
        status: str | None = None,
        thread_ref: str | None = None,
        error: str | None = None,
        whatsapp: str | None = None,
        owner_agent: str | None = None,
    ) -> None:
        props: dict[str, Any] = {}
        if status is not None:
            props[FIELD_FB_OUTREACH_STATUS] = {"select": {"name": status}}
            props[FIELD_FB_OUTREACH_UPDATED] = {"date": {"start": _iso_now()}}
        if thread_ref is not None:
            props[FIELD_FB_THREAD] = {"rich_text": [{"text": {"content": thread_ref[:2000]}}]}
        if error is not None:
            props[FIELD_FB_OUTREACH_ERROR] = {"rich_text": [{"text": {"content": error[:2000]}}]}
        if whatsapp is not None:
            props[FIELD_WHATSAPP] = {"rich_text": [{"text": {"content": whatsapp}}]}
        if owner_agent is not None:
            props[FIELD_OWNER_AGENT] = {"rich_text": [{"text": {"content": owner_agent}}]}
        if props:
            self._req("PATCH", f"/pages/{page_id}", json={"properties": props})


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _rt(prop: dict | None) -> str:
    if not prop:
        return ""
    parts = prop.get("rich_text") or prop.get("title") or []
    return "".join(p.get("plain_text", "") for p in parts).strip()


def _url(prop: dict | None) -> str:
    return (prop or {}).get("url") or ""


def _select(prop: dict | None) -> str:
    sel = (prop or {}).get("select")
    return (sel or {}).get("name") or ""


def extract_listing_id(url: str) -> str:
    m = re.search(r"/marketplace/item/(\d+)", url or "")
    return m.group(1) if m else ""


def is_facebook_object(object_id: str, source_url: str) -> bool:
    if object_id.startswith("F_"):
        return True
    u = (source_url or "").lower()
    return "facebook.com" in u and "marketplace" in u


def resolve_facebook_url(source_url: str, marketplace_post_url: str) -> str:
    for url in (source_url, marketplace_post_url):
        if "facebook.com" in (url or "").lower() and "marketplace" in url.lower():
            return url
    return ""


def is_eligible(listing: NotionListing) -> bool:
    if not is_facebook_object(listing.object_id, listing.facebook_url):
        return False
    if not listing.facebook_url:
        return False
    if listing.outreach_status in TERMINAL_STATUSES:
        return False
    return True


def parse_page(page: dict) -> NotionListing | None:
    props = page.get("properties") or {}
    object_id = _rt(props.get(FIELD_OBJECT_ID))
    if not object_id:
        return None
    source = _url(props.get(FIELD_SOURCE_URL))
    mp_url = _url(props.get(FIELD_FB_MARKETPLACE_URL))
    fb_url = resolve_facebook_url(source, mp_url)
    return NotionListing(
        page_id=page["id"],
        object_id=object_id,
        property_type=_select(props.get(FIELD_HOUSING_TYPE)),
        facebook_url=fb_url,
        listing_id=extract_listing_id(fb_url),
        outreach_status=_select(props.get(FIELD_FB_OUTREACH_STATUS)).lower(),
        whatsapp_existing=_rt(props.get(FIELD_WHATSAPP)),
        owner_agent_existing=_rt(props.get(FIELD_OWNER_AGENT)),
    )


def whatsapp_write_allowed(existing: str, candidate: str) -> tuple[bool, str]:
    if not existing:
        return True, ""
    if existing.strip() == candidate.strip():
        return True, ""
    return False, "conflict"
