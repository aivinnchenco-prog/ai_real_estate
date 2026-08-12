"""Idempotent amoCRM Contact schema reconcile for canonical roles.

Live writes limited to:
  - create contact field «Тип контакта» if missing
  - add missing select enums
  - create missing managed tags CLIENT/OWNER/AGENT

Never updates contacts, leads, pipelines, or messages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .mapping import AMO_FIELD_NAME, AMO_FIELD_VALUES, MANAGED_TAGS
from .roles import CanonicalRole

EXPECTED_FIELD_TYPE = "select"
REQUIRED_ENUMS = tuple(AMO_FIELD_VALUES[r] for r in (
    CanonicalRole.CLIENT,
    CanonicalRole.OWNER,
    CanonicalRole.AGENT,
    CanonicalRole.UNKNOWN,
))
MANAGED_TAG_ORDER = ("CLIENT", "OWNER", "AGENT")

CODE_OK = "OK"
CODE_TYPE_CONFLICT = "AMO_CONTACT_ROLE_FIELD_TYPE_CONFLICT"
CODE_AMBIGUOUS = "AMO_CONTACT_ROLE_FIELD_AMBIGUOUS"
CODE_FAILED = "FAILED"


class AmoSchemaClient(Protocol):
    def list_contact_custom_fields(self) -> list[dict[str, Any]]: ...

    def create_contact_custom_fields(self, fields: list[dict]) -> list[dict]: ...

    def update_contact_custom_fields(self, fields: list[dict]) -> list[dict]: ...

    def list_contact_tags(self) -> list[dict[str, Any]]: ...

    def create_contact_tags(self, names: list[str]) -> list[dict]: ...


@dataclass
class EnumPlanItem:
    value: str
    status: str  # FOUND | WOULD_CREATE | CREATED
    enum_id: int | None = None


@dataclass
class TagPlanItem:
    name: str
    status: str  # FOUND | WOULD_CREATE | CREATED
    tag_id: int | None = None


@dataclass
class SchemaReconcileReport:
    code: str = CODE_OK
    message: str = ""
    field_exists: bool = False
    field_id: int | None = None
    field_type: str = ""
    would_create_field: bool = False
    field_created: bool = False
    enums: list[EnumPlanItem] = field(default_factory=list)
    tags: list[TagPlanItem] = field(default_factory=list)
    writes_performed: list[str] = field(default_factory=list)
    dry_run: bool = True
    contacts_updated: int = 0  # always 0 by design

    def to_text(self) -> str:
        lines = [
            "=== amoCRM contact role schema reconcile ===",
            f"mode: {'DRY-RUN' if self.dry_run else 'APPLY'}",
            f"code: {self.code}",
            f"message: {self.message or '-'}",
            "",
            f"Field: {AMO_FIELD_NAME}",
            f"  exists: {'YES' if self.field_exists else 'NO'}",
            f"  type: {self.field_type or '-'}",
            f"  field_id: {self.field_id or '-'}",
            f"  would_create_field: {'YES' if self.would_create_field else 'NO'}",
            f"  created: {'YES' if self.field_created else 'NO'}",
            "",
            "Enums:",
        ]
        for e in self.enums:
            eid = e.enum_id if e.enum_id is not None else "-"
            lines.append(f"  {e.value}: {e.status} (id={eid})")
        lines.append("")
        lines.append("Tags:")
        for t in self.tags:
            tid = t.tag_id if t.tag_id is not None else "-"
            lines.append(f"  {t.name}: {t.status} (id={tid})")
        lines += [
            "",
            f"writes_performed: {self.writes_performed or ['(none)']}",
            f"contacts_updated: {self.contacts_updated}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field_exists": self.field_exists,
            "field_id": self.field_id,
            "field_type": self.field_type,
            "would_create_field": self.would_create_field,
            "field_created": self.field_created,
            "enums": [
                {"value": e.value, "status": e.status, "enum_id": e.enum_id}
                for e in self.enums
            ],
            "tags": [
                {"name": t.name, "status": t.status, "tag_id": t.tag_id}
                for t in self.tags
            ],
            "writes_performed": list(self.writes_performed),
            "dry_run": self.dry_run,
            "contacts_updated": self.contacts_updated,
        }


def _norm_name(value: str | None) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def find_contact_role_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Exact + normalized-name matches for «Тип контакта»."""
    target = _norm_name(AMO_FIELD_NAME)
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for f in fields or []:
        name = str(f.get("name") or "")
        if _norm_name(name) != target:
            continue
        fid = int(f.get("id") or 0)
        if fid in seen:
            continue
        seen.add(fid)
        out.append(f)
    return out


def _enum_map(field: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in field.get("enums") or []:
        val = str(e.get("value") or "").strip()
        if not val:
            continue
        try:
            out[val] = int(e["id"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _tag_index(tags: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Exact managed tag names only (case-sensitive exact match preferred)."""
    by_exact: dict[str, dict[str, Any]] = {}
    for t in tags or []:
        name = str(t.get("name") or "")
        if name in MANAGED_TAGS and name not in by_exact:
            by_exact[name] = t
    return by_exact


def default_schema_cache_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "amocrm_contact_role_schema.json"


def write_schema_cache(report: SchemaReconcileReport, path: Path | None = None) -> Path:
    path = path or default_schema_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "field_name": AMO_FIELD_NAME,
        "field_id": report.field_id,
        "field_type": report.field_type,
        "enums": {e.value: e.enum_id for e in report.enums},
        "tags": {t.name: t.tag_id for t in report.tags},
        "mapping": {
            "CLIENT": {"field": "Клиент", "tag": "CLIENT"},
            "OWNER": {"field": "Владелец", "tag": "OWNER"},
            "AGENT": {"field": "Агент", "tag": "AGENT"},
            "UNKNOWN": {"field": "Не определено", "tag": None},
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class ContactRoleSchemaReconciler:
    """Plan/apply contact role field + managed tags. No contact data writes."""

    def __init__(self, amo: AmoSchemaClient):
        self.amo = amo

    def reconcile(self, *, apply: bool = False) -> SchemaReconcileReport:
        dry_run = not apply
        report = SchemaReconcileReport(dry_run=dry_run)

        try:
            fields = self.amo.list_contact_custom_fields()
        except Exception as exc:
            report.code = CODE_FAILED
            report.message = f"list_contact_custom_fields failed: {exc}"
            return report

        matches = find_contact_role_fields(fields)
        if len(matches) > 1:
            report.code = CODE_AMBIGUOUS
            report.message = (
                f"multiple fields matching '{AMO_FIELD_NAME}': "
                + ", ".join(f"id={m.get('id')} name={m.get('name')!r}" for m in matches)
            )
            report.field_exists = True
            return report

        if len(matches) == 1:
            field = matches[0]
            report.field_exists = True
            report.field_id = int(field["id"])
            report.field_type = str(field.get("type") or "")
            if report.field_type != EXPECTED_FIELD_TYPE:
                report.code = CODE_TYPE_CONFLICT
                report.message = (
                    f"field_id={report.field_id} type={report.field_type!r} "
                    f"expected={EXPECTED_FIELD_TYPE!r}"
                )
                return report
        else:
            report.field_exists = False
            report.would_create_field = True
            report.field_type = EXPECTED_FIELD_TYPE

        # Enum plan against current or desired field
        current_enums = _enum_map(matches[0]) if matches else {}
        missing_enums: list[str] = []
        for value in REQUIRED_ENUMS:
            if value in current_enums:
                report.enums.append(
                    EnumPlanItem(value=value, status="FOUND", enum_id=current_enums[value])
                )
            else:
                missing_enums.append(value)
                report.enums.append(
                    EnumPlanItem(
                        value=value,
                        status="WOULD_CREATE" if dry_run else "PENDING",
                        enum_id=None,
                    )
                )

        # Tags plan
        try:
            tags = self.amo.list_contact_tags()
        except Exception as exc:
            report.code = CODE_FAILED
            report.message = f"list_contact_tags failed: {exc}"
            return report

        tag_idx = _tag_index(tags)
        missing_tags: list[str] = []
        for name in MANAGED_TAG_ORDER:
            raw = tag_idx.get(name)
            if raw is not None:
                tid = int(raw["id"]) if raw.get("id") is not None else None
                report.tags.append(TagPlanItem(name=name, status="FOUND", tag_id=tid))
            else:
                missing_tags.append(name)
                report.tags.append(
                    TagPlanItem(
                        name=name,
                        status="WOULD_CREATE" if dry_run else "PENDING",
                        tag_id=None,
                    )
                )

        if dry_run:
            report.code = CODE_OK
            report.message = "dry-run plan only — zero writes"
            return report

        # ---- APPLY (schema/tags only) ----
        if report.would_create_field:
            created = self.amo.create_contact_custom_fields(
                [
                    {
                        "name": AMO_FIELD_NAME,
                        "type": EXPECTED_FIELD_TYPE,
                        "enums": [
                            {"value": v, "sort": (i + 1) * 10}
                            for i, v in enumerate(REQUIRED_ENUMS)
                        ],
                    }
                ]
            )
            if not created:
                report.code = CODE_FAILED
                report.message = "create_contact_custom_fields returned empty"
                return report
            field = created[0]
            report.field_id = int(field["id"])
            report.field_type = str(field.get("type") or EXPECTED_FIELD_TYPE)
            report.field_exists = True
            report.field_created = True
            report.writes_performed.append("create_contact_custom_field")
            current_enums = _enum_map(field)
            # refresh enum statuses
            report.enums = [
                EnumPlanItem(
                    value=v,
                    status="CREATED" if v in REQUIRED_ENUMS else "FOUND",
                    enum_id=current_enums.get(v),
                )
                for v in REQUIRED_ENUMS
            ]
            for e in report.enums:
                if e.enum_id is not None:
                    e.status = "CREATED"
            missing_enums = []
        elif missing_enums:
            # PATCH existing field: keep all current enums + add missing
            field = matches[0]
            enums_payload: list[dict[str, Any]] = []
            sort_base = 10
            for e in field.get("enums") or []:
                item: dict[str, Any] = {
                    "id": int(e["id"]),
                    "value": str(e.get("value") or ""),
                    "sort": int(e.get("sort") or sort_base),
                }
                enums_payload.append(item)
                sort_base = max(sort_base, item["sort"] + 10)
            for value in missing_enums:
                enums_payload.append({"value": value, "sort": sort_base})
                sort_base += 10
            updated = self.amo.update_contact_custom_fields(
                [{"id": int(field["id"]), "enums": enums_payload}]
            )
            report.writes_performed.append("update_contact_field_enums")
            refreshed = updated[0] if updated else None
            if refreshed is None:
                # re-read
                fields2 = self.amo.list_contact_custom_fields()
                matches2 = find_contact_role_fields(fields2)
                refreshed = matches2[0] if matches2 else field
            current_enums = _enum_map(refreshed)
            report.enums = []
            for value in REQUIRED_ENUMS:
                status = "CREATED" if value in missing_enums else "FOUND"
                report.enums.append(
                    EnumPlanItem(
                        value=value,
                        status=status,
                        enum_id=current_enums.get(value),
                    )
                )

        if missing_tags:
            created_tags = self.amo.create_contact_tags(missing_tags)
            report.writes_performed.append("create_contact_tags")
            created_by_name = {
                str(t.get("name") or ""): t for t in created_tags
            }
            # re-list to get ids reliably
            tags2 = self.amo.list_contact_tags()
            tag_idx2 = _tag_index(tags2)
            new_tags: list[TagPlanItem] = []
            for name in MANAGED_TAG_ORDER:
                if name in tag_idx2:
                    tid = int(tag_idx2[name]["id"]) if tag_idx2[name].get("id") is not None else None
                    status = "CREATED" if name in missing_tags else "FOUND"
                    new_tags.append(TagPlanItem(name=name, status=status, tag_id=tid))
                elif name in created_by_name:
                    t = created_by_name[name]
                    tid = int(t["id"]) if t.get("id") is not None else None
                    new_tags.append(TagPlanItem(name=name, status="CREATED", tag_id=tid))
                else:
                    new_tags.append(TagPlanItem(name=name, status="WOULD_CREATE", tag_id=None))
            report.tags = new_tags

        # Final verify read
        fields_v = self.amo.list_contact_custom_fields()
        matches_v = find_contact_role_fields(fields_v)
        if len(matches_v) != 1:
            report.code = CODE_AMBIGUOUS if len(matches_v) > 1 else CODE_FAILED
            report.message = "post-apply verification: field not uniquely present"
            return report
        field_v = matches_v[0]
        if str(field_v.get("type") or "") != EXPECTED_FIELD_TYPE:
            report.code = CODE_TYPE_CONFLICT
            report.message = "post-apply verification: type conflict"
            report.field_id = int(field_v["id"])
            report.field_type = str(field_v.get("type") or "")
            return report
        report.field_id = int(field_v["id"])
        report.field_type = str(field_v.get("type") or "")
        enums_v = _enum_map(field_v)
        for e in report.enums:
            e.enum_id = enums_v.get(e.value)
            if e.enum_id is None:
                report.code = CODE_FAILED
                report.message = f"post-apply missing enum: {e.value}"
                return report

        tags_v = _tag_index(self.amo.list_contact_tags())
        for t in report.tags:
            raw = tags_v.get(t.name)
            if raw is None:
                report.code = CODE_FAILED
                report.message = f"post-apply missing tag: {t.name}"
                return report
            t.tag_id = int(raw["id"]) if raw.get("id") is not None else t.tag_id

        report.code = CODE_OK
        report.message = "schema reconciled and verified"
        report.contacts_updated = 0
        return report
