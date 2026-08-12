"""Upload booking DOCX to Cloudflare R2 for Wazzup contentUri sends.

Uses the same SigV4 PUT pattern as assistant-media R2 uploads.
Google Drive is intentionally not used.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


class R2UploadError(RuntimeError):
    pass


def _env(*names: str, default: str = "") -> str:
    for name in names:
        raw = (os.getenv(name) or "").strip()
        if raw:
            return raw
    return default


def r2_config() -> dict[str, str]:
    account = _env("R2_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID")
    access = _env("R2_ACCESS_KEY_ID", "CLOUDFLARE_ACCESS_KEY_ID")
    secret = _env("R2_SECRET_ACCESS_KEY", "CLOUDFLARE_SECRET_ACCESS_KEY")
    bucket = _env("R2_BUCKET", "CLOUDFLARE_BUCKET", default="real-estate-propertiess")
    public = _env(
        "R2_PUBLIC_BASE",
        "CLOUDFLARE_PUBLIC_BASE_URL",
        "R2_PUBLIC_BASE_URL",
    ).rstrip("/")
    return {
        "account_id": account,
        "access_key": access,
        "secret_key": secret,
        "bucket": bucket,
        "public_base": public,
    }


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def upload_bytes_to_r2(
    data: bytes,
    *,
    key: str,
    content_type: str,
    config: dict[str, str] | None = None,
    transport=None,
) -> str:
    """PUT object to R2; return public HTTPS URL (no redirects expected)."""
    cfg = config or r2_config()
    account = cfg["account_id"]
    access = cfg["access_key"]
    secret = cfg["secret_key"]
    bucket = cfg["bucket"]
    public = cfg["public_base"]
    if not (account and access and secret and bucket and public):
        raise R2UploadError(
            "R2 credentials incomplete — set R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/"
            "R2_SECRET_ACCESS_KEY/R2_BUCKET/R2_PUBLIC_BASE "
            "(or CLOUDFLARE_* equivalents)"
        )

    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(data).hexdigest()
    host = f"{account}.r2.cloudflarestorage.com"
    # Path-style: /{bucket}/{key}
    canonical_uri = f"/{bucket}/{key}"
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = (
        f"PUT\n{canonical_uri}\n\n{canonical_headers}\n"
        f"{signed_headers}\n{payload_hash}"
    )
    credential_scope = f"{date_stamp}/auto/s3/aws4_request"
    string_to_sign = (
        "AWS4-HMAC-SHA256\n"
        f"{amz_date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    )
    k_date = _sign(f"AWS4{secret}".encode("utf-8"), date_stamp)
    k_region = _sign(k_date, "auto")
    k_service = _sign(k_region, "s3")
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(
        k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={access}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    url = f"https://{host}{canonical_uri}"
    headers = {
        "Authorization": auth,
        "Content-Type": content_type,
        "Content-Length": str(len(data)),
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    put = transport or urlopen
    req = Request(url, data=data, headers=headers, method="PUT")
    try:
        with put(req, timeout=120) as resp:  # noqa: S310
            status = int(getattr(resp, "status", 200) or 200)
            if status >= 400:
                raise R2UploadError(f"R2 PUT failed HTTP {status}")
    except R2UploadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise R2UploadError(f"R2 PUT failed: {exc}") from exc

    return f"{public}/{quote(key)}"


def upload_booking_docx(
    path: Path | str,
    *,
    object_id: str = "",
    chat_id: str = "",
    config: dict[str, str] | None = None,
    transport=None,
) -> str:
    """Upload local DOCX; return public URL for Wazzup contentUri."""
    local = Path(path)
    if not local.is_file():
        raise R2UploadError(f"docx missing: {local}")
    name = local.name
    if not name.lower().endswith(".docx"):
        name = f"{local.stem}.docx"
    safe_obj = "".join(c for c in (object_id or "booking") if c.isalnum() or c in "-_")
    safe_chat = "".join(c for c in (chat_id or "client") if c.isalnum() or c in "-_")
    key = f"bookings/{safe_obj}/{safe_chat}/{name}"
    content_type = (
        mimetypes.guess_type(name)[0]
        or DOCX_CONTENT_TYPE
    )
    if "wordprocessingml" not in content_type and name.endswith(".docx"):
        content_type = DOCX_CONTENT_TYPE
    return upload_bytes_to_r2(
        local.read_bytes(),
        key=key,
        content_type=content_type,
        config=config,
        transport=transport,
    )
