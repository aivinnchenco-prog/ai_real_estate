from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import AvailabilityConfig
from .models import (
    MonthAvailability,
    PropertySource,
    RefreshStatus,
    RefreshTier,
    SourceKind,
    SourceStatus,
    TargetFieldMapping,
)
from .notion_reader import NOTION_API, NOTION_VERSION, _plain_property
from .models import NotionSchema


class NotionWriteBlocked(RuntimeError):
    """Raised when Notion writes are disabled by dry-run or AVAILABILITY_ENABLED=false."""


class SyncOneWriteRefused(RuntimeError):
    """Raised when sync-one narrow write safety rules block the operation."""


SYNC_ONE_ALLOWED_OBJECT_IDS = frozenset({"A_20260802_002"})
REFRESH_ONE_ALLOWED_OBJECT_IDS = frozenset({"A_20260807_001", "A_20260802_002"})


class NotionWriter:
    """Notion writer with global safety gates and a narrow sync-one path."""

    def __init__(self, config: AvailabilityConfig) -> None:
        self.config = config
        self.writes_attempted = 0
        self.writes_performed = 0
        self.source_writes_performed = 0

    def _guard(self) -> None:
        self.writes_attempted += 1
        if self.config.dry_run or not self.config.enabled:
            reason = []
            if self.config.dry_run:
                reason.append("AVAILABILITY_DRY_RUN=true")
            if not self.config.enabled:
                reason.append("AVAILABILITY_ENABLED=false")
            raise NotionWriteBlocked(
                "Notion writes are blocked (" + ", ".join(reason) + ")"
            )

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
    ) -> dict[str, Any]:
        import json
        import urllib.error
        import urllib.request

        url = f"{NOTION_API}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Notion {method} {path} HTTP {exc.code}: {body[:800]}") from exc

    def upsert_availability_row(
        self,
        source: PropertySource,
        months: list[MonthAvailability],
    ) -> None:
        self._guard()
        raise RuntimeError("Live Notion upsert is not implemented in V1")

    def create_properties(self, properties: dict) -> None:
        self._guard()
        raise RuntimeError("Notion schema mutation is not implemented in V1")

    def delete_properties(self, names: list[str]) -> None:
        self._guard()
        raise RuntimeError("Notion schema mutation is not implemented in V1")

    def rename_property(self, old_name: str, new_name: str) -> None:
        self._guard()
        raise RuntimeError("Notion schema mutation is not implemented in V1")

    def _narrow_guard(self, object_id: str, target_database_id: str) -> None:
        if object_id not in SYNC_ONE_ALLOWED_OBJECT_IDS:
            raise SyncOneWriteRefused(
                f"sync-one write refused: object_id {object_id!r} not in allowlist"
            )
        if target_database_id.replace("-", "") == self.config.source_database_id.replace("-", ""):
            raise SyncOneWriteRefused(
                "sync-one write refused: writes to source «Аренда недвижимости» are forbidden"
            )
        if target_database_id.replace("-", "") != self.config.target_database_id.replace("-", ""):
            raise SyncOneWriteRefused("sync-one write refused: target database mismatch")

    def _prop_value(self, prop_type: str, value: str | None) -> dict:
        if prop_type == "title":
            if not value:
                return {"title": []}
            return {"title": [{"type": "text", "text": {"content": value}}]}
        if prop_type == "rich_text":
            if not value:
                return {"rich_text": []}
            return {"rich_text": [{"type": "text", "text": {"content": value}}]}
        if prop_type == "select":
            if not value:
                return {"select": None}
            return {"select": {"name": value}}
        if prop_type == "status":
            if not value:
                return {"status": None}
            return {"status": {"name": value}}
        if prop_type == "date":
            if not value:
                return {"date": None}
            return {"date": {"start": value}}
        if prop_type == "number":
            if value is None or value == "":
                return {"number": None}
            return {"number": float(value)}
        if prop_type == "url":
            if not value:
                return {"url": None}
            return {"url": value}
        if not value:
            return {"rich_text": []}
        return {"rich_text": [{"type": "text", "text": {"content": str(value)}}]}

    def _build_properties(
        self,
        mapping: TargetFieldMapping,
        schema_properties: dict[str, dict],
        *,
        object_id: str,
        object_name: str,
        source: SourceKind,
        calendar_url: str,
        month_cells: dict[str, str],
        last_checked: datetime | None,
        next_check: datetime | None,
        refresh_tier: RefreshTier,
        refresh_status: RefreshStatus,
        source_status: SourceStatus,
        last_error: str,
    ) -> dict[str, dict]:
        """Build page properties in frozen column order (months → technical)."""
        field_values: dict[str, str | None] = {}

        def store(match, value: str | None) -> None:
            if match and match.status == "mapped" and match.property_name:
                field_values[match.property_name] = value

        store(mapping.object_id, object_id)
        store(mapping.object_name, object_name)
        store(mapping.source, source.value)
        store(mapping.calendar_url, calendar_url or "")
        store(mapping.refresh_tier, refresh_tier.value)
        store(mapping.refresh_status, refresh_status.value)
        store(mapping.source_status, source_status.value)
        store(mapping.last_error, last_error or "")

        if mapping.last_checked and mapping.last_checked.property_name:
            field_values[mapping.last_checked.property_name] = (
                last_checked.isoformat() if last_checked else None
            )
        if mapping.next_check and mapping.next_check.property_name:
            field_values[mapping.next_check.property_name] = (
                next_check.isoformat() if next_check else None
            )
        for month_name in mapping.month_columns:
            field_values[month_name] = month_cells.get(month_name, "")

        props: dict[str, dict] = {}
        for col_name in mapping.display_column_order():
            if col_name not in field_values:
                continue
            ptype = str((schema_properties.get(col_name) or {}).get("type") or "rich_text")
            props[col_name] = self._prop_value(ptype, field_values.get(col_name))
        return props

    def _object_id_filter(
        self,
        mapping: TargetFieldMapping,
        object_id: str,
        schema_properties: dict[str, dict],
    ) -> dict:
        match = mapping.object_id
        if not match or not match.property_name:
            raise RuntimeError("Object ID property not mapped in target schema")
        ptype = str((schema_properties.get(match.property_name) or {}).get("type") or "rich_text")
        if ptype == "title":
            return {"property": match.property_name, "title": {"equals": object_id}}
        return {"property": match.property_name, "rich_text": {"equals": object_id}}

    def count_target_rows_by_object_id(
        self,
        database_id: str,
        mapping: TargetFieldMapping,
        object_id: str,
        schema_properties: dict[str, dict],
    ) -> int:
        filter_body = self._object_id_filter(mapping, object_id, schema_properties)
        data = self._request(
            "POST",
            f"/databases/{database_id}/query",
            {"filter": filter_body, "page_size": 100},
        )
        return len(data.get("results") or [])

    def find_target_pages(
        self,
        database_id: str,
        mapping: TargetFieldMapping,
        object_id: str,
        schema_properties: dict[str, dict],
    ) -> list[dict]:
        filter_body = self._object_id_filter(mapping, object_id, schema_properties)
        data = self._request(
            "POST",
            f"/databases/{database_id}/query",
            {"filter": filter_body, "page_size": 100},
        )
        return list(data.get("results") or [])

    def read_target_row(
        self,
        database_id: str,
        mapping: TargetFieldMapping,
        object_id: str,
        schema_properties: dict[str, dict],
    ) -> dict[str, str]:
        pages = self.find_target_pages(database_id, mapping, object_id, schema_properties)
        if not pages:
            return {}
        page = pages[0]
        props = page.get("properties") or {}
        out: dict[str, str] = {}
        for month_name in mapping.month_columns:
            out[month_name] = _plain_property(props.get(month_name))
        for name in mapping.technical_field_names():
            out[name] = _plain_property(props.get(name))
        return out

    def _refresh_narrow_guard(self, object_id: str, target_database_id: str) -> None:
        if object_id not in REFRESH_ONE_ALLOWED_OBJECT_IDS:
            raise SyncOneWriteRefused(
                f"refresh-one write refused: object_id {object_id!r} not in allowlist"
            )
        if target_database_id.replace("-", "") == self.config.source_database_id.replace("-", ""):
            raise SyncOneWriteRefused(
                "refresh-one write refused: writes to source «Аренда недвижимости» are forbidden"
            )
        if target_database_id.replace("-", "") != self.config.target_database_id.replace("-", ""):
            raise SyncOneWriteRefused("refresh-one write refused: target database mismatch")

    def _build_pricing_refresh_properties(
        self,
        mapping: TargetFieldMapping,
        schema_properties: dict[str, dict],
        *,
        month_cells: dict[str, str],
        last_checked: datetime | None,
        next_check: datetime | None,
        refresh_status: RefreshStatus,
        last_error: str,
        refresh_tier: RefreshTier | None = None,
        source_status: SourceStatus | None = None,
    ) -> dict[str, dict]:
        field_values: dict[str, str | None] = {}
        for month_name in mapping.month_columns:
            field_values[month_name] = month_cells.get(month_name, "")
        if mapping.last_checked and mapping.last_checked.property_name:
            field_values[mapping.last_checked.property_name] = (
                last_checked.isoformat() if last_checked else None
            )
        if mapping.next_check and mapping.next_check.property_name:
            field_values[mapping.next_check.property_name] = (
                next_check.isoformat() if next_check else None
            )
        if mapping.refresh_status and mapping.refresh_status.property_name:
            field_values[mapping.refresh_status.property_name] = refresh_status.value
        if mapping.last_error and mapping.last_error.property_name:
            field_values[mapping.last_error.property_name] = last_error or ""
        if refresh_tier is not None and mapping.refresh_tier and mapping.refresh_tier.property_name:
            field_values[mapping.refresh_tier.property_name] = refresh_tier.value
        if source_status is not None and mapping.source_status and mapping.source_status.property_name:
            field_values[mapping.source_status.property_name] = source_status.value

        allowed = set(mapping.month_columns)
        for match in (
            mapping.last_checked,
            mapping.next_check,
            mapping.refresh_status,
            mapping.last_error,
            mapping.refresh_tier,
            mapping.source_status,
        ):
            if match and match.property_name:
                allowed.add(match.property_name)

        props: dict[str, dict] = {}
        for col_name in allowed:
            if col_name not in field_values:
                continue
            ptype = str((schema_properties.get(col_name) or {}).get("type") or "rich_text")
            props[col_name] = self._prop_value(ptype, field_values.get(col_name))
        return props

    def _batch_narrow_guard(
        self,
        object_id: str,
        batch_allowed_ids: frozenset[str],
        target_database_id: str,
    ) -> None:
        if object_id not in batch_allowed_ids:
            raise SyncOneWriteRefused(
                f"batch write refused: object_id {object_id!r} not in batch allowlist"
            )
        if target_database_id.replace("-", "") == self.config.source_database_id.replace("-", ""):
            raise SyncOneWriteRefused(
                "batch write refused: writes to source «Аренда недвижимости» are forbidden"
            )
        if target_database_id.replace("-", "") != self.config.target_database_id.replace("-", ""):
            raise SyncOneWriteRefused("batch write refused: target database mismatch")

    def sync_batch_notion_upsert(
        self,
        *,
        object_id: str,
        object_name: str,
        source: SourceKind,
        calendar_url: str,
        batch_allowed_ids: frozenset[str],
        month_cells: dict[str, str],
        last_checked: datetime | None,
        next_check: datetime | None,
        refresh_status: RefreshStatus,
        last_error: str,
        target_schema: NotionSchema,
        target_mapping: TargetFieldMapping,
        refresh_tier: RefreshTier = RefreshTier.H48,
        source_status: SourceStatus | None = None,
    ) -> tuple[str, str]:
        """Batch path: update existing row or create one if missing."""
        self._batch_narrow_guard(object_id, batch_allowed_ids, target_schema.database_id)
        self.writes_attempted += 1

        pages = self.find_target_pages(
            target_schema.database_id,
            target_mapping,
            object_id,
            target_schema.properties,
        )
        if len(pages) > 1:
            raise SyncOneWriteRefused(f"duplicate rows for {object_id}")

        refresh_props = self._build_pricing_refresh_properties(
            target_mapping,
            target_schema.properties,
            month_cells=month_cells,
            last_checked=last_checked,
            next_check=next_check,
            refresh_status=refresh_status,
            last_error=last_error,
            refresh_tier=refresh_tier,
            source_status=source_status,
        )

        if pages:
            page_id = pages[0]["id"]
            self._request("PATCH", f"/pages/{page_id}", {"properties": refresh_props})
            self.writes_performed += 1
            return "updated", page_id

        create_source_status = source_status
        if create_source_status is None and source == SourceKind.AIRBNB:
            create_source_status = SourceStatus.ACTIVE
        full_props = self._build_properties(
            target_mapping,
            target_schema.properties,
            object_id=object_id,
            object_name=object_name,
            source=source,
            calendar_url=calendar_url,
            month_cells=month_cells,
            last_checked=last_checked,
            next_check=next_check,
            refresh_tier=refresh_tier,
            refresh_status=refresh_status,
            source_status=create_source_status or SourceStatus.UNKNOWN,
            last_error=last_error,
        )
        payload = {
            "parent": {"database_id": target_schema.database_id},
            "properties": full_props,
        }
        created = self._request("POST", "/pages", payload)
        self.writes_performed += 1
        return "created", str(created.get("id") or "")

    def sync_batch_notion_update(
        self,
        *,
        object_id: str,
        batch_allowed_ids: frozenset[str],
        month_cells: dict[str, str],
        last_checked: datetime | None,
        next_check: datetime | None,
        refresh_status: RefreshStatus,
        last_error: str,
        target_schema: NotionSchema,
        target_mapping: TargetFieldMapping,
    ) -> tuple[str, str]:
        """Batch path: update month cells + refresh timestamps on existing row."""
        self._batch_narrow_guard(object_id, batch_allowed_ids, target_schema.database_id)
        self.writes_attempted += 1

        pages = self.find_target_pages(
            target_schema.database_id,
            target_mapping,
            object_id,
            target_schema.properties,
        )
        if not pages:
            raise SyncOneWriteRefused(f"batch refused: no Notion row for {object_id}")
        if len(pages) > 1:
            raise SyncOneWriteRefused(f"duplicate rows for {object_id}")

        properties = self._build_pricing_refresh_properties(
            target_mapping,
            target_schema.properties,
            month_cells=month_cells,
            last_checked=last_checked,
            next_check=next_check,
            refresh_status=refresh_status,
            last_error=last_error,
        )
        page_id = pages[0]["id"]
        self._request("PATCH", f"/pages/{page_id}", {"properties": properties})
        self.writes_performed += 1
        return "updated", page_id

    def sync_one_month_cells_only(
        self,
        *,
        object_id: str,
        month_cells: dict[str, str],
        target_schema: NotionSchema,
        target_mapping: TargetFieldMapping,
    ) -> tuple[str, str]:
        """Update only month column cells on an existing target row."""
        self._refresh_narrow_guard(object_id, target_schema.database_id)
        self.writes_attempted += 1

        pages = self.find_target_pages(
            target_schema.database_id,
            target_mapping,
            object_id,
            target_schema.properties,
        )
        if not pages:
            raise SyncOneWriteRefused(
                f"month-display write refused: no existing Notion row for {object_id}"
            )
        if len(pages) > 1:
            raise SyncOneWriteRefused(
                f"duplicate Object ID rows in target: {len(pages)} for {object_id}"
            )

        props: dict[str, dict] = {}
        for month_name in target_mapping.month_columns:
            ptype = str(
                (target_schema.properties.get(month_name) or {}).get("type") or "rich_text"
            )
            props[month_name] = self._prop_value(ptype, month_cells.get(month_name, ""))

        page_id = pages[0]["id"]
        self._request("PATCH", f"/pages/{page_id}", {"properties": props})
        self.writes_performed += 1
        return "updated", page_id

    def sync_one_pricing_refresh(
        self,
        *,
        object_id: str,
        month_cells: dict[str, str],
        last_checked: datetime | None,
        next_check: datetime | None,
        refresh_status: RefreshStatus,
        last_error: str,
        target_schema: NotionSchema,
        target_mapping: TargetFieldMapping,
    ) -> tuple[str, str]:
        """Update existing target row: month cells + refresh timestamps only."""
        self._refresh_narrow_guard(object_id, target_schema.database_id)
        self.writes_attempted += 1

        pages = self.find_target_pages(
            target_schema.database_id,
            target_mapping,
            object_id,
            target_schema.properties,
        )
        if not pages:
            raise SyncOneWriteRefused(
                f"refresh-one refused: no existing Notion row for {object_id}"
            )
        if len(pages) > 1:
            raise SyncOneWriteRefused(
                f"duplicate Object ID rows in target: {len(pages)} for {object_id}"
            )

        properties = self._build_pricing_refresh_properties(
            target_mapping,
            target_schema.properties,
            month_cells=month_cells,
            last_checked=last_checked,
            next_check=next_check,
            refresh_status=refresh_status,
            last_error=last_error,
        )
        page_id = pages[0]["id"]
        self._request("PATCH", f"/pages/{page_id}", {"properties": properties})
        self.writes_performed += 1
        return "updated", page_id

    def sync_one_upsert(
        self,
        *,
        object_id: str,
        object_name: str,
        source: SourceKind,
        calendar_url: str,
        month_cells: dict[str, str],
        last_checked: datetime | None,
        next_check: datetime | None,
        refresh_tier: RefreshTier,
        refresh_status: RefreshStatus,
        source_status: SourceStatus,
        last_error: str,
        target_schema: NotionSchema,
        target_mapping: TargetFieldMapping,
    ) -> tuple[str, str]:
        """Narrow write path: one allowlisted object, target DB only."""
        self._narrow_guard(object_id, target_schema.database_id)
        self.writes_attempted += 1

        properties = self._build_properties(
            target_mapping,
            target_schema.properties,
            object_id=object_id,
            object_name=object_name,
            source=source,
            calendar_url=calendar_url,
            month_cells=month_cells,
            last_checked=last_checked,
            next_check=next_check,
            refresh_tier=refresh_tier,
            refresh_status=refresh_status,
            source_status=source_status,
            last_error=last_error,
        )

        pages = self.find_target_pages(
            target_schema.database_id,
            target_mapping,
            object_id,
            target_schema.properties,
        )
        if len(pages) > 1:
            raise SyncOneWriteRefused(
                f"duplicate Object ID rows in target: {len(pages)} for {object_id}"
            )

        if pages:
            page_id = pages[0]["id"]
            self._request("PATCH", f"/pages/{page_id}", {"properties": properties})
            self.writes_performed += 1
            return "updated", page_id

        payload = {
            "parent": {"database_id": target_schema.database_id},
            "properties": properties,
        }
        created = self._request("POST", "/pages", payload)
        self.writes_performed += 1
        return "created", str(created.get("id") or "")
