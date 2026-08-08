"""ASGI health/auth tests."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from amocrm_mcp.asgi import create_app
from amocrm_mcp.config import Settings


@pytest.fixture
def app():
    settings = Settings(
        amo_subdomain="test",
        amo_access_token="token",
        mcp_host="127.0.0.1",
        mcp_port=8787,
        mcp_path="/mcp",
        mcp_api_token="mcp-secret",
        mcp_transport="streamable-http",
        cache_ttl_seconds=600,
    )
    return create_app(settings)


def test_health_no_auth(app):
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert "token" not in r.text.lower()


def test_mcp_requires_bearer(app):
    client = TestClient(app)
    r = client.get("/mcp")
    assert r.status_code == 401
