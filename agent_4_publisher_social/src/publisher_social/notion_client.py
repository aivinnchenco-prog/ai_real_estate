from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

NOTION_VERSION = "2022-06-28"
USER_AGENT = "publisher-social/0.1"


def _headers() -> dict[str, str]:
    key = os.environ.get("NOTION_API_KEY")
    if not key:
        raise ValueError("Set NOTION_API_KEY in .env")
    return {
        "Authorization": f"Bearer {key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def req(
    method: str,
    url: str,
    data: bytes | None = None,
) -> Any:
    request = urllib.request.Request(url, data=data, method=method)
    for k, v in _headers().items():
        request.add_header(k, v)
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            if not body:
                return {}
            return json.loads(body)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {url}: {err_body}") from e


def get_prop(page: dict[str, Any], name: str, ptype: str) -> Any:
    props = page.get("properties", {})
    prop = props.get(name, {})
    if prop.get("type") != ptype:
        return None
    if ptype == "url":
        return prop.get("url")
    if ptype == "status":
        st = prop.get("status") or {}
        return st.get("name")
    if ptype == "title":
        items = prop.get("title") or []
        return "".join(t.get("plain_text", "") for t in items)
    if ptype == "rich_text":
        items = prop.get("rich_text") or []
        return "".join(t.get("plain_text", "") for t in items)
    if ptype == "checkbox":
        return bool(prop.get("checkbox"))
    if ptype == "date":
        date = prop.get("date") or {}
        return date.get("start")
    if ptype == "number":
        return prop.get("number")
    if ptype == "select":
        sel = prop.get("select") or {}
        return sel.get("name")
    return None


def get_page(page_id: str) -> dict[str, Any]:
    return req("GET", f"https://api.notion.com/v1/pages/{page_id}")


def query_ready(
    database_id: str,
    status_ready: str,
    status_field: str,
) -> list[dict[str, Any]]:
    payload = json.dumps(
        {
            "filter": {
                "property": status_field,
                "status": {"equals": status_ready},
            }
        }
    ).encode("utf-8")
    result = req(
        "POST",
        f"https://api.notion.com/v1/databases/{database_id}/query",
        payload,
    )
    return result.get("results", [])


def query_latest_published(
    database_id: str,
    published_at_field: str,
    *,
    page_size: int = 1,
) -> list[dict[str, Any]]:
    """Страницы с заполненной датой публикации, сначала самые свежие."""
    payload = json.dumps(
        {
            "filter": {
                "property": published_at_field,
                "date": {"is_not_empty": True},
            },
            "sorts": [
                {"property": published_at_field, "direction": "descending"},
            ],
            "page_size": page_size,
        }
    ).encode("utf-8")
    result = req(
        "POST",
        f"https://api.notion.com/v1/databases/{database_id}/query",
        payload,
    )
    return result.get("results", [])


def update_fields(page_id: str, properties: dict[str, Any]) -> None:
    payload = json.dumps({"properties": properties}).encode("utf-8")
    req("PATCH", f"https://api.notion.com/v1/pages/{page_id}", payload)


def checkbox_prop(checked: bool) -> dict[str, Any]:
    return {"checkbox": checked}


def url_prop(url: str | None) -> dict[str, Any]:
    return {"url": url}


def rich_text_prop(text: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}


def date_prop(start_iso: str, *, time_zone: str | None = None) -> dict[str, Any]:
    """Notion date (+ optional time). start_iso — ISO-8601, лучше с timezone."""
    date: dict[str, Any] = {"start": start_iso}
    if time_zone:
        date["time_zone"] = time_zone
    return {"date": date}


def database_id() -> str:
    db = os.environ.get("NOTION_DB_ID")
    if not db:
        raise ValueError("Set NOTION_DB_ID in .env")
    return db
