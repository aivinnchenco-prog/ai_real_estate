"""amoCRM contact role mirror (Тип контакта + managed tags). Dry-run safe."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..mapping import (
    AMO_FIELD_NAME,
    MANAGED_TAGS,
    amo_field_value,
    amo_tag_for_role,
)
from ..phone import normalize_phone_digits, normalize_phone_e164
from ..roles import CanonicalRole


class AmoContactClient(Protocol):
    def find_contacts(self, query: str) -> list[dict[str, Any]]: ...

    def get_contact(self, contact_id: int) -> dict[str, Any]: ...

    def list_contact_custom_fields(self) -> list[dict[str, Any]]: ...

    def update_contact(self, contact_id: int, payload: dict[str, Any]) -> Any: ...


@dataclass
class AmoRoleSyncResult:
    code: str
    message: str = ""
    contact_id: int | None = None
    dry_run: bool = True
    planned: dict[str, Any] = field(default_factory=dict)
    wrote: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "contact_id": self.contact_id,
            "dry_run": self.dry_run,
            "planned": self.planned,
            "wrote": self.wrote,
        }


def _phones_from_contact(contact: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for cf in contact.get("custom_fields_values") or []:
        code = str(cf.get("field_code") or "").upper()
        if code != "PHONE":
            continue
        for v in cf.get("values") or []:
            digits = normalize_phone_digits(str(v.get("value") or ""))
            if digits:
                out.add(digits)
    return out


def resolve_contact_by_phone(
    amo: AmoContactClient, phone: str | None
) -> tuple[str, int | None, list[int]]:
    """Exact E.164 digit match. Never pick at random among multiples."""
    phone_n = normalize_phone_e164(phone)
    digits = normalize_phone_digits(phone)
    if not phone_n or not digits:
        return "CONTACT_NOT_FOUND", None, []

    queries = [phone_n, digits, f"+{digits}"]
    seen_ids: set[int] = set()
    candidates: list[dict[str, Any]] = []
    for q in queries:
        for c in amo.find_contacts(q) or []:
            cid = int(c["id"])
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            candidates.append(c)

    exact: list[dict[str, Any]] = []
    for c in candidates:
        phones = _phones_from_contact(c)
        if digits in phones:
            exact.append(c)

    if not exact:
        # Fallback: single search hit with no phone fields exposed — still unsafe
        # if >1; if exactly 1 candidate from query, treat as match only when
        # query was digits-equal (amo often returns fuzzy). Require exact.
        return "CONTACT_NOT_FOUND", None, [int(c["id"]) for c in candidates]

    if len(exact) > 1:
        return "AMBIGUOUS_CONTACT", None, [int(c["id"]) for c in exact]

    return "OK", int(exact[0]["id"]), [int(exact[0]["id"])]


def plan_tag_update(
    existing_tags: list[dict[str, Any]] | list[str],
    role: CanonicalRole,
) -> list[dict[str, str]]:
    """Keep unrelated tags; replace managed CLIENT/OWNER/AGENT."""
    names: list[str] = []
    for t in existing_tags or []:
        if isinstance(t, dict):
            name = str(t.get("name") or "")
        else:
            name = str(t)
        if name and name not in MANAGED_TAGS:
            names.append(name)
    tag = amo_tag_for_role(role)
    if tag:
        names.append(tag)
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append({"name": n})
    return out


def find_contact_type_field(
    fields: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for f in fields or []:
        if str(f.get("name") or "").strip() == AMO_FIELD_NAME:
            return f
    return None


class AmoContactRoleSync:
    """Mirror canonical role → Тип контакта + managed tags. No other writes."""

    def __init__(
        self,
        amo: AmoContactClient | None = None,
        *,
        dry_run: bool | None = None,
        enabled: bool | None = None,
    ):
        self.amo = amo
        if dry_run is None:
            dry_run = (os.getenv("CONTACT_ROLE_AMO_DRY_RUN") or "true").lower() in {
                "1",
                "true",
                "yes",
            }
        if enabled is None:
            enabled = (os.getenv("CONTACT_ROLE_AMO_SYNC_ENABLED") or "false").lower() in {
                "1",
                "true",
                "yes",
            }
        self.dry_run = bool(dry_run)
        self.enabled = bool(enabled)

    def sync_role(
        self,
        *,
        phone: str | None,
        role: CanonicalRole | str,
        contact_id: int | None = None,
        confirm_write: bool = False,
    ) -> AmoRoleSyncResult:
        role_n = CanonicalRole.parse(role)
        dry = self.dry_run or not confirm_write or not self.enabled

        if self.amo is None:
            return AmoRoleSyncResult(
                code="AMO_UNAVAILABLE",
                message="amo client not configured",
                dry_run=dry,
            )

        resolved_id = contact_id
        if resolved_id is None:
            code, resolved_id, _ids = resolve_contact_by_phone(self.amo, phone)
            if code == "AMBIGUOUS_CONTACT":
                return AmoRoleSyncResult(
                    code="AMBIGUOUS_CONTACT",
                    message="multiple exact phone matches — refuse guess",
                    dry_run=dry,
                )
            if code != "OK" or resolved_id is None:
                return AmoRoleSyncResult(
                    code="CONTACT_NOT_FOUND",
                    message="no exact E.164 contact match",
                    dry_run=dry,
                )

        contact = self.amo.get_contact(resolved_id)
        fields = self.amo.list_contact_custom_fields()
        type_field = find_contact_type_field(fields)
        if type_field is None:
            return AmoRoleSyncResult(
                code="FIELD_MISSING",
                message=(
                    f"amoCRM contact field '{AMO_FIELD_NAME}' not found — "
                    "create manually with enums Клиент/Владелец/Агент/Не определено"
                ),
                contact_id=resolved_id,
                dry_run=dry,
            )

        desired_value = amo_field_value(role_n)
        field_id = int(type_field["id"])

        # Current field value
        current_value = ""
        for cf in contact.get("custom_fields_values") or []:
            if int(cf.get("field_id") or 0) == field_id:
                vals = cf.get("values") or []
                if vals:
                    current_value = str(vals[0].get("value") or "")
                break

        embedded = (contact.get("_embedded") or {}).get("tags") or []
        planned_tags = plan_tag_update(embedded, role_n)
        current_tag_names = {
            str(t.get("name") or "") for t in embedded if isinstance(t, dict)
        }
        planned_tag_names = {t["name"] for t in planned_tags}
        managed_current = current_tag_names & MANAGED_TAGS
        managed_planned = planned_tag_names & MANAGED_TAGS

        already = current_value == desired_value and managed_current == managed_planned
        planned = {
            "field": AMO_FIELD_NAME,
            "value": desired_value,
            "tags": planned_tags,
            "preserve_other_tags": True,
        }
        if already:
            return AmoRoleSyncResult(
                code="ALREADY_SYNCED",
                message="amoCRM already mirrors canonical role",
                contact_id=resolved_id,
                dry_run=dry,
                planned=planned,
            )

        if dry:
            return AmoRoleSyncResult(
                code="DRY_RUN_PLAN",
                message="dry-run: no amoCRM write",
                contact_id=resolved_id,
                dry_run=True,
                planned=planned,
            )

        # LIVE write path — only field + tags. Never called in default/tests.
        enum_id = None
        for e in type_field.get("enums") or []:
            if str(e.get("value") or "").strip() == desired_value:
                try:
                    enum_id = int(e["id"])
                except (KeyError, TypeError, ValueError):
                    enum_id = None
                break
        value_payload: dict[str, Any]
        if enum_id is not None:
            value_payload = {"enum_id": enum_id}
        else:
            value_payload = {"value": desired_value}
        payload = {
            "custom_fields_values": [
                {"field_id": field_id, "values": [value_payload]}
            ],
            "_embedded": {"tags": planned_tags},
        }
        self.amo.update_contact(resolved_id, payload)
        return AmoRoleSyncResult(
            code="SYNCED",
            message="amoCRM contact role updated",
            contact_id=resolved_id,
            dry_run=False,
            planned=planned,
            wrote=True,
        )


class FakeAmoContactClient:
    """In-memory amo stub for offline tests (no network)."""

    def __init__(
        self,
        contacts: list[dict[str, Any]] | None = None,
        *,
        has_type_field: bool = True,
        fail_update: bool = False,
    ):
        self.contacts = {int(c["id"]): c for c in (contacts or [])}
        self.has_type_field = has_type_field
        self.fail_update = fail_update
        self.updates: list[tuple[int, dict[str, Any]]] = []

    def find_contacts(self, query: str) -> list[dict[str, Any]]:
        q_digits = normalize_phone_digits(query) or ""
        out = []
        for c in self.contacts.values():
            phones = _phones_from_contact(c)
            if q_digits and q_digits in phones:
                out.append(c)
            elif query and query in str(c.get("name") or ""):
                out.append(c)
        return out

    def get_contact(self, contact_id: int) -> dict[str, Any]:
        return dict(self.contacts[int(contact_id)])

    def list_contact_custom_fields(self) -> list[dict[str, Any]]:
        if not self.has_type_field:
            return []
        return [
            {
                "id": 9001,
                "name": AMO_FIELD_NAME,
                "type": "select",
                "enums": [
                    {"id": 1, "value": "Клиент"},
                    {"id": 2, "value": "Владелец"},
                    {"id": 3, "value": "Агент"},
                    {"id": 4, "value": "Не определено"},
                ],
            }
        ]

    def update_contact(self, contact_id: int, payload: dict[str, Any]) -> Any:
        if self.fail_update:
            raise RuntimeError("amo 500")
        self.updates.append((contact_id, payload))
        c = self.contacts[contact_id]
        if "custom_fields_values" in payload:
            # merge type field
            existing = list(c.get("custom_fields_values") or [])
            by_id = {int(x.get("field_id") or 0): x for x in existing}
            for item in payload["custom_fields_values"]:
                by_id[int(item["field_id"])] = item
            # preserve PHONE
            c["custom_fields_values"] = list(by_id.values())
        if "_embedded" in payload and "tags" in payload["_embedded"]:
            emb = dict(c.get("_embedded") or {})
            emb["tags"] = payload["_embedded"]["tags"]
            c["_embedded"] = emb
        self.contacts[contact_id] = c
        return c


def contact_with_phone(
    contact_id: int,
    phone: str,
    *,
    tags: list[str] | None = None,
    type_value: str = "",
    extra_tags: list[str] | None = None,
) -> dict[str, Any]:
    digits = normalize_phone_e164(phone) or phone
    cf: list[dict[str, Any]] = [
        {
            "field_code": "PHONE",
            "field_id": 1,
            "values": [{"value": digits, "enum_code": "WORK"}],
        }
    ]
    if type_value:
        cf.append(
            {
                "field_id": 9001,
                "field_name": AMO_FIELD_NAME,
                "values": [{"value": type_value}],
            }
        )
    tag_list = list(tags or []) + list(extra_tags or [])
    return {
        "id": contact_id,
        "name": f"Contact {contact_id}",
        "custom_fields_values": cf,
        "_embedded": {"tags": [{"name": t} for t in tag_list]},
    }
