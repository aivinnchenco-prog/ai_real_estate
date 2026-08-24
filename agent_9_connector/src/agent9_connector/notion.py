"""Notion adapter for Agent 9 (no imports from Agents 1-8)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import requests

# Existing Notion fields (read + write where noted)
FIELD_OBJECT_ID = "Объект ID"
FIELD_HOUSING_TYPE = "Тип жилья"
FIELD_SOURCE_URL = "Источник объявления"
FIELD_WHATSAPP = "WhatsApp контакт"
FIELD_OWNER_AGENT_TYPE = "Агент/Владелец (тип)"
OWNER_AGENT_TYPE_OPTIONS = frozenset({"Владелец", "Агент"})


@dataclass
class NotionListing:
    page_id: str
    object_id: str
    property_type: str
    facebook_url: str
    listing_id: str
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
        self._property_names: set[str] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.token and self.database_id)

    def _database_property_names(self) -> set[str]:
        if self._property_names is None:
            data = self._req("GET", f"/databases/{self.database_id}")
            self._property_names = set((data.get("properties") or {}).keys())
        return self._property_names

    def _filter_existing_props(self, props: dict[str, Any]) -> dict[str, Any]:
        names = self._database_property_names()
        return {key: value for key, value in props.items() if key in names}

    def _req(self, method: str, path: str, **kwargs) -> dict:
        r = requests.request(method, f"{self.base}{path}", headers=self.headers, timeout=30, **kwargs)
        r.raise_for_status()
        return r.json() if r.text else {}

    def query_all_pages(self) -> list[dict]:
        """Paginated database query (all rows)."""
        if not self.configured:
            return []
        pages: list[dict] = []
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            data = self._req("POST", f"/databases/{self.database_id}/query", json=body)
            pages.extend(data.get("results") or [])
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return pages

    def query_eligible(self) -> list[NotionListing]:
        if not self.configured:
            return []
        results: list[NotionListing] = []
        for page in self.query_all_pages():
            listing = parse_page(page)
            if listing and is_eligible(listing):
                results.append(listing)
        return results

    def update_contact_fields(
        self,
        page_id: str,
        *,
        whatsapp: str | None = None,
        owner_agent: str | None = None,
    ) -> None:
        """Write only WhatsApp and owner/agent type — no new Notion columns."""
        props: dict[str, Any] = {}
        if whatsapp is not None:
            props[FIELD_WHATSAPP] = {"rich_text": [{"text": {"content": whatsapp}}]}
        if owner_agent is not None:
            props[FIELD_OWNER_AGENT_TYPE] = {"select": {"name": owner_agent}}
        props = self._filter_existing_props(props)
        if props:
            self._req("PATCH", f"/pages/{page_id}", json={"properties": props})


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


def resolve_facebook_url(source_url: str) -> str:
    url = (source_url or "").strip()
    if "facebook.com" in url.lower() and "marketplace" in url.lower():
        return url
    return ""


def is_eligible(listing: NotionListing) -> bool:
    if not is_facebook_object(listing.object_id, listing.facebook_url):
        return False
    if not listing.facebook_url:
        return False
    if listing.whatsapp_existing and listing.owner_agent_existing:
        return False
    return True


def parse_page(page: dict) -> NotionListing | None:
    props = page.get("properties") or {}
    object_id = _rt(props.get(FIELD_OBJECT_ID))
    if not object_id:
        return None
    fb_url = resolve_facebook_url(_url(props.get(FIELD_SOURCE_URL)))
    return NotionListing(
        page_id=page["id"],
        object_id=object_id,
        property_type=_select(props.get(FIELD_HOUSING_TYPE)),
        facebook_url=fb_url,
        listing_id=extract_listing_id(fb_url),
        whatsapp_existing=_rt(props.get(FIELD_WHATSAPP)),
        owner_agent_existing=_select(props.get(FIELD_OWNER_AGENT_TYPE)),
    )


def whatsapp_write_allowed(existing: str, candidate: str) -> tuple[bool, str]:
    if not existing:
        return True, ""
    if existing.strip() == candidate.strip():
        return True, ""
    return False, "conflict"
