"""Normalization tests."""

from __future__ import annotations

from amocrm_mcp.normalize import (
    normalize_account,
    normalize_contact_summary,
    normalize_custom_field,
    normalize_lead_summary,
    normalize_pipeline,
    normalize_task,
    wrap_data,
)


def test_account_normalization():
    raw = {"id": 1, "name": "Open Home", "subdomain": "openhome", "timezone": "Asia/Bangkok"}
    out = normalize_account(raw)
    assert out["subdomain"] == "openhome"
    assert "token" not in out


def test_pipeline_statuses():
    raw = {
        "id": 10,
        "name": "Rent",
        "_embedded": {"statuses": [{"id": 1, "name": "New", "sort": 10, "color": "#fff"}]},
    }
    out = normalize_pipeline(raw)
    assert out["statuses"][0]["name"] == "New"


def test_custom_field_enums():
    raw = {"id": 5, "name": "Pets", "type": "select", "enums": [{"id": 1, "value": "yes"}]}
    out = normalize_custom_field(raw)
    assert out["enums"][0]["value"] == "yes"


def test_lead_summary_custom_fields():
    raw = {
        "id": 77,
        "name": "Deal",
        "custom_fields_values": [
            {"field_name": "Объект ID", "values": [{"value": "F_1"}]},
        ],
        "_embedded": {"contacts": [{"id": 3, "is_main": True}], "tags": []},
    }
    out = normalize_lead_summary(raw)
    assert out["custom_fields"]["Объект ID"] == "F_1"


def test_contact_phones():
    raw = {
        "id": 1,
        "name": "John",
        "custom_fields_values": [
            {"field_code": "PHONE", "values": [{"value": "+661234"}]},
            {"field_code": "EMAIL", "values": [{"value": "a@b.c"}]},
        ],
        "_embedded": {"leads": [{"id": 9}]},
    }
    out = normalize_contact_summary(raw)
    assert out["phones"] == ["+661234"]


def test_task_normalization():
    raw = {"id": 1, "text": "call", "is_completed": False, "entity_id": 9, "entity_type": "leads"}
    assert normalize_task(raw)["entity_id"] == 9


def test_wrap_data_metadata():
    payload = wrap_data({"ok": True})
    assert payload["source"] == "amocrm"
    assert payload["content_type"] == "crm_data"
