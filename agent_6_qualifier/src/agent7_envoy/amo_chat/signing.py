"""Official amoCRM Chat API (amojo) request signing.

Contract (amocrm.ru /developers/content/chats/chat-start):
Date, Content-Type, Content-MD5, X-Signature
X-Signature = HMAC-SHA1(method\\nmd5\\ncontent-type\\ndate\\npath, channel_secret)
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Mapping


def content_md5(body: bytes | str) -> str:
    raw = body.encode("utf-8") if isinstance(body, str) else body
    return hashlib.md5(raw).hexdigest().lower()


def rfc2822_now() -> str:
    return format_datetime(datetime.now(timezone.utc))


def build_signature_string(
    *,
    method: str,
    content_md5_hex: str,
    content_type: str,
    date: str,
    path: str,
) -> str:
    """Canonical string for HMAC-SHA1 (new signing scheme)."""
    return "\n".join(
        [
            method.upper(),
            content_md5_hex or "",
            content_type or "",
            date or "",
            path or "",
        ]
    )


def sign_request(
    *,
    method: str,
    body: bytes | str,
    path: str,
    secret: str,
    content_type: str = "application/json",
    date: str | None = None,
) -> dict[str, str]:
    """Return headers Date / Content-Type / Content-MD5 / X-Signature (lowercase hashes)."""
    if not secret:
        raise ValueError("channel secret required for Chat API signing")
    raw = body.encode("utf-8") if isinstance(body, str) else body
    date_hdr = date or rfc2822_now()
    md5_hex = content_md5(raw)
    sig_str = build_signature_string(
        method=method,
        content_md5_hex=md5_hex,
        content_type=content_type,
        date=date_hdr,
        path=path,
    )
    digest = hmac.new(
        secret.encode("utf-8"),
        sig_str.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest().lower()
    return {
        "Date": date_hdr,
        "Content-Type": content_type,
        "Content-MD5": md5_hex,
        "X-Signature": digest,
    }


def verify_signature(
    *,
    method: str,
    body: bytes | str,
    path: str,
    secret: str,
    headers: Mapping[str, str],
) -> bool:
    """Verify Chat API / webhook request signature with constant-time compare."""
    if not secret:
        return False
    # Header names are case-insensitive
    norm = {str(k).lower(): str(v) for k, v in headers.items()}
    date = norm.get("date", "")
    content_type = norm.get("content-type", "application/json")
    provided = (norm.get("x-signature") or "").lower().strip()
    if not provided or not date:
        return False
    expected_headers = sign_request(
        method=method,
        body=body,
        path=path,
        secret=secret,
        content_type=content_type,
        date=date,
    )
    return hmac.compare_digest(provided, expected_headers["X-Signature"])
