"""Configuration for amoCRM MCP."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@dataclass(frozen=True)
class Settings:
    amo_subdomain: str
    amo_access_token: str
    mcp_host: str
    mcp_port: int
    mcp_path: str
    mcp_api_token: str
    mcp_transport: str
    cache_ttl_seconds: int
    default_limit: int = 20
    max_limit: int = 50

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            amo_subdomain=_env("AMO_MCP_SUBDOMAIN"),
            amo_access_token=_env("AMO_MCP_ACCESS_TOKEN"),
            mcp_host=_env("MCP_HOST", "0.0.0.0"),
            mcp_port=int(_env("MCP_PORT", "8787")),
            mcp_path=_env("MCP_STREAMABLE_HTTP_PATH", "/mcp"),
            mcp_api_token=_env("MCP_API_TOKEN"),
            mcp_transport=_env("MCP_TRANSPORT", "streamable-http"),
            cache_ttl_seconds=int(_env("MCP_CACHE_TTL_SECONDS", "600")),
        )

    def amo_configured(self) -> bool:
        return bool(self.amo_subdomain and self.amo_access_token)
