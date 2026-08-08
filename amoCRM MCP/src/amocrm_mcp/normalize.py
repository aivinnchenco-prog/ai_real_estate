"""Normalize amoCRM payloads into compact MCP-safe JSON."""

from __future__ import annotations

from typing import Any


def wrap_data(payload: Any, *, include_raw: bool = False, raw: Any | None = None) -> dict:
    body: dict[str, Any] = {
        "source": "amocrm",
        "content_type": "crm_data",
        "data": payload,
    }
    if include_raw and raw is not None:
        body["raw"] = raw
    return body


def normalize_account(raw: dict) -> dict:
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "subdomain": raw.get("subdomain"),
        "timezone": raw.get("timezone"),
        "country": raw.get("country"),
        "currency": raw.get("currency"),
        "language": raw.get("language"),
        "task_types": (raw.get("_embedded") or {}).get("task_types"),
    }


def normalize_user(raw: dict) -> dict:
    rights = raw.get("rights") or {}
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "email": raw.get("email"),
        "is_active": raw.get("is_active"),
        "is_admin": rights.get("is_admin"),
        "group_id": raw.get("group_id"),
        "role_id": raw.get("role_id"),
    }


def normalize_pipeline(raw: dict) -> dict:
    statuses = []
    for status in (raw.get("_embedded") or {}).get("statuses", []):
        statuses.append({
            "id": status.get("id"),
            "name": status.get("name"),
            "sort": status.get("sort"),
            "color": status.get("color"),
            "type": status.get("type"),
        })
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "sort": raw.get("sort"),
        "is_main": raw.get("is_main"),
        "statuses": statuses,
    }


def normalize_custom_field(raw: dict) -> dict:
    enums = []
    for item in raw.get("enums") or []:
        enums.append({
            "id": item.get("id"),
            "value": item.get("value"),
            "sort": item.get("sort"),
        })
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "code": raw.get("code"),
        "type": raw.get("type"),
        "sort": raw.get("sort"),
        "group_id": raw.get("group_id"),
        "enums": enums,
    }


def _custom_fields_map(values: list[dict] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in values or []:
        name = field.get("field_name") or str(field.get("field_id"))
        vals = [v.get("value") for v in (field.get("values") or [])]
        out[name] = vals[0] if len(vals) == 1 else vals
    return out


def normalize_lead_summary(raw: dict) -> dict:
    contacts = []
    for contact in (raw.get("_embedded") or {}).get("contacts", []):
        contacts.append({"id": contact.get("id"), "is_main": contact.get("is_main")})
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "pipeline_id": raw.get("pipeline_id"),
        "status_id": raw.get("status_id"),
        "responsible_user_id": raw.get("responsible_user_id"),
        "price": raw.get("price"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "closed_at": raw.get("closed_at"),
        "loss_reason_id": raw.get("loss_reason_id"),
        "tags": [t.get("name") for t in (raw.get("_embedded") or {}).get("tags", [])],
        "custom_fields": _custom_fields_map(raw.get("custom_fields_values")),
        "contacts": contacts,
    }


def normalize_lead_detail(raw: dict) -> dict:
    detail = normalize_lead_summary(raw)
    companies = []
    for company in (raw.get("_embedded") or {}).get("companies", []):
        companies.append({"id": company.get("id")})
    detail["companies"] = companies
    return detail


def normalize_contact_summary(raw: dict) -> dict:
    phones, emails = [], []
    for field in raw.get("custom_fields_values") or []:
        code = (field.get("field_code") or "").upper()
        values = [v.get("value") for v in (field.get("values") or []) if v.get("value")]
        if code == "PHONE":
            phones.extend(values)
        if code == "EMAIL":
            emails.extend(values)
    leads = [{"id": l.get("id")} for l in (raw.get("_embedded") or {}).get("leads", [])]
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "first_name": raw.get("first_name"),
        "last_name": raw.get("last_name"),
        "responsible_user_id": raw.get("responsible_user_id"),
        "phones": phones,
        "emails": emails,
        "tags": [t.get("name") for t in (raw.get("_embedded") or {}).get("tags", [])],
        "custom_fields": _custom_fields_map(raw.get("custom_fields_values")),
        "linked_leads": leads,
    }


def normalize_task(raw: dict) -> dict:
    return {
        "id": raw.get("id"),
        "text": raw.get("text"),
        "complete_till": raw.get("complete_till"),
        "is_completed": raw.get("is_completed"),
        "responsible_user_id": raw.get("responsible_user_id"),
        "task_type_id": raw.get("task_type_id"),
        "entity_id": raw.get("entity_id"),
        "entity_type": raw.get("entity_type"),
        "result": raw.get("result"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
    }


def normalize_note(raw: dict) -> dict:
    params = raw.get("params") or {}
    return {
        "id": raw.get("id"),
        "note_type": raw.get("note_type"),
        "text": params.get("text"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "created_by": raw.get("created_by"),
    }


def normalize_chat_binding(raw: dict) -> dict:
    return {
        "id": raw.get("id"),
        "contact_id": raw.get("contact_id"),
        "chat_id": raw.get("chat_id"),
    }


def normalize_link(raw: dict) -> dict:
    return {
        "to_entity_id": raw.get("to_entity_id"),
        "to_entity_type": raw.get("to_entity_type"),
        "metadata": raw.get("metadata"),
    }
