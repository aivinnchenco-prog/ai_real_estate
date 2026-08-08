"""Read-only guard tests."""

from __future__ import annotations

import pytest

from amocrm_mcp.amo_client import ReadOnlyAmoClient
from amocrm_mcp.errors import ReadOnlyViolation


@pytest.fixture
def client():
    return ReadOnlyAmoClient("test", "token123")


def test_get_allowed(client, monkeypatch):
    monkeypatch.setattr(
        "amocrm_mcp.amo_client.requests.get",
        lambda *a, **k: type("R", (), {"status_code": 200, "text": "{}", "headers": {}, "json": lambda self=None: {}})(),
    )
    assert client.request("GET", "/account") == {}


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_non_get_blocked(client, method):
    with pytest.raises(ReadOnlyViolation):
        client.request(method, "/leads")
