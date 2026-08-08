"""Security and auth tests."""

from __future__ import annotations

from amocrm_mcp.auth import redact_secrets
from amocrm_mcp.config import Settings
from amocrm_mcp.errors import AuthError
from amocrm_mcp.amo_client import ReadOnlyAmoClient


def test_token_redacted():
    secret = "supersecrettoken123"
    text = f"Authorization: Bearer {secret}"
    assert secret not in redact_secrets(text, token=secret)
    assert "***" in redact_secrets(text, token=secret)


def test_missing_credentials():
    try:
        ReadOnlyAmoClient("", "")
    except AuthError as exc:
        assert "AMO_MCP_SUBDOMAIN" in str(exc)
    else:
        raise AssertionError("expected AuthError")


def test_logs_do_not_include_authorization():
    text = redact_secrets("Authorization: Bearer abcdef", token="abcdef")
    assert "abcdef" not in text
    assert "***" in text


def test_settings_defaults():
    s = Settings(
        amo_subdomain="x",
        amo_access_token="y",
        mcp_host="127.0.0.1",
        mcp_port=8787,
        mcp_path="/mcp",
        mcp_api_token="z",
        mcp_transport="streamable-http",
        cache_ttl_seconds=600,
    )
    assert s.max_limit == 50
