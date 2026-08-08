"""MCP server tool registration tests."""

from __future__ import annotations

import json

import pytest

from amocrm_mcp.config import Settings
from amocrm_mcp.server import build_server


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("AMO_MCP_SUBDOMAIN", "test")
    monkeypatch.setenv("AMO_MCP_ACCESS_TOKEN", "token")
    settings = Settings.from_env()
    return build_server(settings)


@pytest.mark.asyncio
async def test_tools_discoverable(server):
    tools = await server.list_tools()
    names = {t.name for t in tools}
    expected = {
        "amo_get_account",
        "amo_list_users",
        "amo_get_user",
        "amo_list_pipelines",
        "amo_get_pipeline",
        "amo_list_lead_fields",
        "amo_list_contact_fields",
        "amo_search_leads",
        "amo_get_lead",
        "amo_get_lead_links",
        "amo_search_contacts",
        "amo_get_contact",
        "amo_get_contact_chats",
        "amo_list_tasks",
        "amo_get_lead_tasks",
        "amo_get_lead_notes",
        "amo_get_deal_overview",
    }
    assert expected.issubset(names)
    assert not any(n.startswith("amo_create") or n.startswith("amo_update") for n in names)


@pytest.mark.asyncio
async def test_tool_descriptions_read_only(server):
    tools = await server.list_tools()
    for tool in tools:
        assert "READ-ONLY" in (tool.description or "").upper()


@pytest.mark.asyncio
async def test_invalid_arguments_rejected(server):
    try:
        await server.call_tool("amo_get_lead", {"lead_id": "x"})
        raised = False
    except Exception:
        raised = True
    assert raised
