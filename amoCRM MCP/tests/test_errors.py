"""HTTP error mapping tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from amocrm_mcp.amo_client import ReadOnlyAmoClient
from amocrm_mcp.errors import AuthError, NotFoundError, PermissionDeniedError, RateLimitedError


def _resp(status, text="", headers=None):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.headers = headers or {}
    r.json = MagicMock(side_effect=ValueError)
    return r


@pytest.fixture
def client():
    return ReadOnlyAmoClient("test", "tok")


@pytest.mark.parametrize("status,exc", [(401, AuthError), (403, PermissionDeniedError), (404, NotFoundError), (429, RateLimitedError)])
def test_http_errors(client, monkeypatch, status, exc):
    monkeypatch.setattr("amocrm_mcp.amo_client.requests.get", lambda *a, **k: _resp(status, "err"))
    with pytest.raises(exc):
        client.request("GET", "/account")
