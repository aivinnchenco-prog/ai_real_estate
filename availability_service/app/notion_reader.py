from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode, urlparse

from .config import AvailabilityConfig
from .models import (
    FieldMatch,
    NotionSchema,
    PropertySource,
    SourceFieldMapping,
    SourceKind,
    TargetFieldMapping,
)
from .target_column_order import (
    LOCKED_MONTH_COLUMNS,
    LOCKED_TECHNICAL_COLUMNS,
    locked_columns_present,
    validate_locked_target_schema,
)

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

OBJECT_ID_ALIASES = {"объект id", "object id"}
NAME_ALIASES = {"название объекта", "name", "title"}
SOURCE_EXACT = {"source", "источник"}
SOURCE_URL_ALIASES = {"source url", "источник объявления", "listing url", "source_url"}
CALENDAR_URL_ALIASES = {"календарь", "calendar"}

MONTH_NAME_RE = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s*'?-?(\d{2}|\d{4})$"
    r"|^(янв|фев|мар|апр|май|июн|июл|авг|сен|окт|ноя|дек)[а-я]*\s*'?-?(\d{2}|\d{4})$"
    r"|^\d{4}-\d{2}$"
    r"|^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}$",
    re.IGNORECASE,
)
TECHNICAL_NAME_RE = re.compile(
    r"(object id|объект id|source url|источник|status|статус|updated|checked|"
    r"error|retry|tier|refresh|url|id$|name|название|last_|next_)",
    re.IGNORECASE,
)


def _norm_name(name: str) -> str:
    return " ".join(name.replace("ё", "е").replace("Ё", "Е").strip().lower().split())


def _title_plain(title: list[dict] | None) -> str:
    return "".join(part.get("plain_text", "") for part in (title or []))


def _plain_property(prop: dict | None) -> str:
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype in ("rich_text", "title"):
        return "".join(x.get("plain_text", "") for x in prop.get(ptype) or [])
    if ptype == "url":
        return prop.get("url") or ""
    if ptype == "date":
        date_val = prop.get("date") or {}
        return str(date_val.get("start") or "")
    if ptype in ("select", "status"):
        value = prop.get(ptype) or {}
        return value.get("name") or ""
    if ptype == "unique_id":
        uid = prop.get("unique_id") or {}
        prefix = uid.get("prefix") or ""
        number = uid.get("number")
        if number is None:
            return prefix
        return f"{prefix}-{number}" if prefix else str(number)
    if ptype == "number":
        number = prop.get("number")
        return "" if number is None else str(number)
    return ""


def normalize_source(*, raw_source: str = "", source_url: str = "", object_id: str = "") -> SourceKind:
    blob = " ".join([raw_source, source_url, object_id]).lower()
    if "airbnb" in blob or object_id.upper().startswith("A_"):
        return SourceKind.AIRBNB
    if "facebook" in blob or "fb.com" in blob or object_id.upper().startswith("F_"):
        return SourceKind.FACEBOOK
    host = urlparse(source_url).netloc.lower()
    if "airbnb." in host:
        return SourceKind.AIRBNB
    if "facebook." in host or host.endswith("fb.com") or "fbcdn." in host:
        return SourceKind.FACEBOOK
    return SourceKind.UNKNOWN


def resolve_calendar_check_url(
    *,
    calendar_url: str = "",
    source_url: str = "",
    source: SourceKind = SourceKind.UNKNOWN,
) -> str:
    """URL для проверки доступности: «Календарь» в CRM, для Airbnb — fallback на источник."""
    cal = calendar_url.strip()
    if cal and cal.lower() not in {"ручной", "manual"}:
        return cal
    listing = source_url.strip()
    if source == SourceKind.AIRBNB and listing:
        return listing
    return listing or cal


def _unique_match(properties: dict[str, dict], aliases: set[str], *, logical: str) -> FieldMatch:
    hits: list[tuple[str, str, str]] = []
    for name, meta in properties.items():
        if _norm_name(name) in aliases:
            hits.append((name, str(meta.get("type") or "unknown"), str(meta.get("id") or "")))
    if len(hits) == 1:
        name, ptype, pid = hits[0]
        return FieldMatch(logical, name, ptype, "mapped", (name,), pid or None)
    if len(hits) > 1:
        return FieldMatch(
            logical,
            None,
            None,
            "ambiguous",
            tuple(item[0] for item in hits),
        )
    return FieldMatch(logical, None, None, "unmapped", ())


def _exact_match(properties: dict[str, dict], exact_name: str, logical: str) -> FieldMatch:
    if exact_name in properties:
        meta = properties[exact_name]
        return FieldMatch(
            logical,
            exact_name,
            str(meta.get("type") or "unknown"),
            "mapped",
            (exact_name,),
            str(meta.get("id") or "") or None,
        )
    close = [n for n in properties if _norm_name(n) == _norm_name(exact_name)]
    if len(close) == 1:
        meta = properties[close[0]]
        return FieldMatch(
            logical,
            close[0],
            str(meta.get("type") or "unknown"),
            "mapped",
            (close[0],),
            str(meta.get("id") or "") or None,
        )
    if len(close) > 1:
        return FieldMatch(logical, None, None, "ambiguous", tuple(close))
    return FieldMatch(logical, None, None, "unmapped", ())


def map_target_fields(properties: dict[str, dict]) -> TargetFieldMapping:
    """Map target DB columns by exact names from live schema. Never guess."""
    month_columns, technical_columns = classify_target_properties(properties)
    mapping = TargetFieldMapping(
        month_columns=month_columns,
        technical_columns=technical_columns,
    )
    mapping.object_id = _exact_match(properties, "Object ID", "object_id")
    if mapping.object_id.status != "mapped":
        titles = [n for n, m in properties.items() if m.get("type") == "title"]
        if len(titles) == 1:
            meta = properties[titles[0]]
            mapping.object_id = FieldMatch(
                "object_id", titles[0], str(meta.get("type")), "mapped", (titles[0],),
                str(meta.get("id") or "") or None,
            )
    mapping.object_name = _exact_match(properties, "Объект", "object_name")
    mapping.source = _exact_match(properties, "Source", "source")
    mapping.calendar_url = _exact_match(properties, "URL объекта календаря", "calendar_url")
    mapping.last_checked = _exact_match(properties, "Last Checked", "last_checked")
    mapping.next_check = _exact_match(properties, "Next Check", "next_check")
    mapping.refresh_tier = _exact_match(properties, "Refresh Tier", "refresh_tier")
    mapping.refresh_status = _exact_match(properties, "Refresh Status", "refresh_status")
    mapping.source_status = _exact_match(properties, "Source Status", "source_status")
    mapping.last_error = _exact_match(properties, "Last Error", "last_error")
    return mapping


def map_source_fields(properties: dict[str, dict]) -> SourceFieldMapping:
    """Map Object ID / Name / Source / Source URL from live schema. Never guess."""
    mapping = SourceFieldMapping()
    mapping.object_id = _unique_match(properties, OBJECT_ID_ALIASES, logical="object_id")
    mapping.name = _unique_match(properties, NAME_ALIASES, logical="name")
    if mapping.name.status != "mapped":
        titles = [
            (name, str(meta.get("type") or "unknown"), str(meta.get("id") or ""))
            for name, meta in properties.items()
            if meta.get("type") == "title"
        ]
        if len(titles) == 1:
            name, ptype, pid = titles[0]
            mapping.name = FieldMatch("name", name, ptype, "mapped", (name,), pid or None)
        elif len(titles) > 1:
            mapping.name = FieldMatch(
                "name", None, None, "ambiguous", tuple(item[0] for item in titles)
            )
    mapping.source_url = _unique_match(properties, SOURCE_URL_ALIASES, logical="source_url")
    mapping.calendar_url = _unique_match(properties, CALENDAR_URL_ALIASES, logical="calendar_url")
    mapping.source = _unique_match(properties, SOURCE_EXACT, logical="source")
    return mapping


def classify_target_properties(properties: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Split schema into month vs technical using frozen column registry."""
    names = set(properties.keys())
    month_columns = [c for c in LOCKED_MONTH_COLUMNS if c in names]
    technical_columns = [c for c in LOCKED_TECHNICAL_COLUMNS if c in names]
    if len(month_columns) == len(LOCKED_MONTH_COLUMNS) and len(technical_columns) == len(
        LOCKED_TECHNICAL_COLUMNS
    ):
        return list(LOCKED_MONTH_COLUMNS), list(LOCKED_TECHNICAL_COLUMNS)
    month_fallback: list[str] = []
    technical_fallback: list[str] = []
    for name, meta in properties.items():
        ptype = str(meta.get("type") or "")
        normalized = _norm_name(name)
        if MONTH_NAME_RE.match(normalized):
            if name not in month_fallback:
                month_fallback.append(name)
            continue
        if name not in technical_fallback:
            technical_fallback.append(name)
    return sort_month_columns(month_fallback), sort_technical_columns_legacy(technical_fallback)


def sort_technical_columns_legacy(names: list[str]) -> list[str]:
    ordered = [c for c in LOCKED_TECHNICAL_COLUMNS if c in names]
    for name in names:
        if name not in ordered:
            ordered.append(name)
    return ordered


def sort_technical_columns(names: list[str]) -> list[str]:
    return sort_technical_columns_legacy(names)


def target_display_column_order(
    month_columns: list[str],
    technical_columns: list[str],
) -> list[str]:
    names = set(month_columns) | set(technical_columns)
    return locked_columns_present(names)


MONTH_ABBR_MAP = {abbr.lower(): idx + 1 for idx, abbr in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
)}


def parse_month_column_label(name: str) -> tuple[int, int] | None:
    normalized = _norm_name(name)
    if re.fullmatch(r"\d{4}-\d{2}", normalized):
        year_s, month_s = normalized.split("-")
        return int(year_s), int(month_s)
    parts = normalized.split()
    if len(parts) < 2:
        return None
    month = MONTH_ABBR_MAP.get(parts[0][:3])
    if month is None:
        return None
    year_token = parts[1].replace("'", "")
    yy = int(year_token)
    year = yy if yy >= 100 else 2000 + yy
    return year, month


def sort_month_columns(names: list[str]) -> list[str]:
    keyed: list[tuple[tuple[int, int], str]] = []
    for name in names:
        parsed = parse_month_column_label(name)
        keyed.append((parsed or (9999, 99), name))
    keyed.sort(key=lambda item: item[0])
    return [name for _, name in keyed]


def compare_month_schema(
    window: list,
    notion_month_columns: list[str],
) -> tuple[bool, str]:
    calculated = [item.display_name for item in window]
    notion_sorted = sort_month_columns(notion_month_columns)
    if calculated == notion_sorted:
        return True, "PASS"
    return False, (
        f"FAIL: calculated={calculated!r} notion={notion_sorted!r}"
    )


class NotionReader:
    def __init__(self, config: AvailabilityConfig) -> None:
        self.config = config

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.notion_api_key}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        query: list[tuple[str, str]] | None = None,
        version: str = NOTION_VERSION,
    ) -> dict[str, Any]:
        url = f"{NOTION_API}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.config.notion_api_key}",
            "Notion-Version": version,
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Notion {method} {path} HTTP {exc.code}: {body[:800]}") from exc

    def fetch_schema(self, database_id: str) -> NotionSchema:
        if not database_id:
            return NotionSchema(database_id="", title="", error="database id is empty")
        try:
            data = self._request("GET", f"/databases/{database_id}")
        except RuntimeError as exc:
            message = str(exc)
            if "is a page, not a database" in message:
                return self._schema_from_page(database_id, message)
            return NotionSchema(database_id=database_id, title="", error=message)
        title = _title_plain(data.get("title"))
        schema = NotionSchema(
            database_id=database_id,
            title=title,
            properties=data.get("properties") or {},
            object_kind=data.get("object") or "database",
        )
        schema.data_source_id = self._fetch_data_source_id(database_id)
        return schema

    def _fetch_data_source_id(self, database_id: str) -> str:
        if (
            self.config.target_data_source_id
            and database_id.replace("-", "") == self.config.target_database_id.replace("-", "")
        ):
            return self.config.target_data_source_id
        try:
            data = self._request("GET", f"/databases/{database_id}", version="2025-09-03")
        except RuntimeError:
            return ""
        sources = data.get("data_sources") or []
        if sources:
            return str(sources[0].get("id") or "")
        return ""

    def _schema_from_page(self, page_id: str, original_error: str) -> NotionSchema:
        try:
            page = self._request("GET", f"/pages/{page_id}")
        except RuntimeError as exc:
            return NotionSchema(database_id=page_id, title="", error=str(exc))
        props = page.get("properties") or {}
        title = ""
        for meta in props.values():
            if meta.get("type") == "title":
                title = _plain_property(meta)
                break
        children: list[str] = []
        try:
            kids = self._request("GET", f"/blocks/{page_id}/children?page_size=100")
            for block in kids.get("results") or []:
                btype = block.get("type")
                extra = ""
                if btype == "child_database":
                    extra = (block.get("child_database") or {}).get("title") or ""
                children.append(f"{btype}:{block.get('id')} {extra}".strip())
        except RuntimeError:
            children = []
        note = (
            f"{original_error} Resolved as page title={title!r}. "
            f"Children: {children or ['(none)']}. "
            "This is not the target database «Аренда недвижимости — Доступность»."
        )
        return NotionSchema(
            database_id=page_id,
            title=title,
            properties=props,
            object_kind="page",
            error=note,
        )

    def read_property_batch(
        self,
        database_id: str,
        mapping: SourceFieldMapping,
        *,
        limit: int = 25,
    ) -> list[PropertySource]:
        if not database_id:
            return []
        filter_ids = [
            match.property_id
            for match in mapping.as_list()
            if match.status == "mapped" and match.property_id
        ]
        query = [("filter_properties", pid) for pid in filter_ids]
        results: list[dict] = []
        remaining = limit
        cursor = None
        while remaining > 0:
            payload: dict[str, Any] = {"page_size": min(remaining, 100)}
            if cursor:
                payload["start_cursor"] = cursor
            data = self._request(
                "POST",
                f"/databases/{database_id}/query",
                payload,
                query=query or None,
            )
            batch = data.get("results") or []
            results.extend(batch)
            remaining -= len(batch)
            if not data.get("has_more") or not batch:
                break
            cursor = data.get("next_cursor")
        items: list[PropertySource] = []
        for page in results[:limit]:
            props = page.get("properties") or {}
            object_id = _plain_property(props.get(mapping.object_id.property_name) if mapping.object_id and mapping.object_id.property_name else None)
            name = _plain_property(props.get(mapping.name.property_name) if mapping.name and mapping.name.property_name else None)
            raw_source = ""
            if mapping.source and mapping.source.property_name:
                raw_source = _plain_property(props.get(mapping.source.property_name))
            source_url = ""
            if mapping.source_url and mapping.source_url.property_name:
                source_url = _plain_property(props.get(mapping.source_url.property_name))
            if not object_id and not name and not source_url:
                continue
            items.append(
                PropertySource(
                    object_id=object_id.strip(),
                    name=name.strip(),
                    source=normalize_source(
                        raw_source=raw_source,
                        source_url=source_url,
                        object_id=object_id,
                    ),
                    source_url=source_url.strip(),
                    notion_page_id=page.get("id") or "",
                )
            )
        return items
