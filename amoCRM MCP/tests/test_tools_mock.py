"""Tool layer tests with mocked amo client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from amocrm_mcp.config import Settings
from amocrm_mcp.errors import InvalidArgumentError
from amocrm_mcp.tools import AmoTools


@pytest.fixture
def settings():
    return Settings(
        amo_subdomain="test",
        amo_access_token="token",
        mcp_host="127.0.0.1",
        mcp_port=8787,
        mcp_path="/mcp",
        mcp_api_token="secret",
        mcp_transport="streamable-http",
        cache_ttl_seconds=600,
        default_limit=20,
        max_limit=50,
    )


@pytest.fixture
def tools(settings):
    client = MagicMock()
    return AmoTools(client, settings), client


def test_search_leads_limit(tools):
    svc, client = tools
    client.search_leads.return_value = [{"id": 1, "name": "x", "_embedded": {"tags": [], "contacts": []}}]
    out = svc.amo_search_leads(limit=100)
    client.search_leads.assert_called_once()
    assert client.search_leads.call_args.kwargs["limit"] == 50
    assert out["data"]["items"][0]["id"] == 1


def test_limit_invalid(tools):
    svc, _ = tools
    with pytest.raises(InvalidArgumentError):
        svc.amo_search_contacts(limit=0)


def test_get_lead_links(tools):
    svc, client = tools
    client.get_lead_links.return_value = [{"to_entity_id": 5, "to_entity_type": "contacts"}]
    out = svc.amo_get_lead_links(10)
    assert out["data"][0]["to_entity_type"] == "contacts"


def test_contact_chats(tools):
    svc, client = tools
    client.get_contact_chats.return_value = [{"id": 1, "contact_id": 2, "chat_id": "uuid"}]
    out = svc.amo_get_contact_chats(2)
    assert out["data"][0]["chat_id"] == "uuid"


def test_list_tasks_filter(tools):
    svc, client = tools
    client.list_tasks.return_value = [{"id": 1, "text": "t", "entity_id": 9, "entity_type": "leads"}]
    svc.amo_get_lead_tasks(9)
    client.get_lead_tasks.assert_called_once_with(9, limit=20)


def test_notes_pagination(tools):
    svc, client = tools
    client.get_lead_notes.return_value = [{"id": 1, "note_type": "common", "params": {"text": "hi"}}]
    out = svc.amo_get_lead_notes(1, limit=5)
    assert out["data"]["limit"] == 5


def test_deal_overview_aggregates(tools):
    svc, client = tools
    client.get_lead.return_value = {"id": 1, "name": "d", "_embedded": {"contacts": [{"id": 2}], "tags": []}}
    client.get_lead_links.return_value = []
    client.get_lead_tasks.return_value = []
    client.get_lead_notes.return_value = []
    client.get_contact_chats.return_value = [{"id": 9, "contact_id": 2, "chat_id": "c"}]
    out = svc.amo_get_deal_overview(1)
    assert out["data"]["lead"]["id"] == 1
    assert out["data"]["chat_bindings"][0]["chat_id"] == "c"
    client.get_lead.assert_called_once()
    client.get_contact_chats.assert_called_once()


def test_include_raw_disabled_by_default(tools):
    svc, client = tools
    client.list_pipelines.return_value = [{"id": 1, "name": "p", "_embedded": {"statuses": []}}]
    out = svc.amo_list_pipelines()
    assert "raw" not in out
