"""Auth helpers and secret redaction."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9._\-]+)", re.I)


def redact_secrets(text: str, *, token: str = "") -> str:
    if not text:
        return text
    out = _TOKEN_RE.sub(r"\1***", text)
    if token and len(token) > 8:
        out = out.replace(token, "***")
    return out


def require_amo_credentials(subdomain: str, token: str) -> None:
    missing = []
    if not subdomain:
        missing.append("AMO_MCP_SUBDOMAIN")
    if not token:
        missing.append("AMO_MCP_ACCESS_TOKEN")
    if missing:
        from .errors import AuthError
        raise AuthError(f"Missing credentials: {', '.join(missing)}")
