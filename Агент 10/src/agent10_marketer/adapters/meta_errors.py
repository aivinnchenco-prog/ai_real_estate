"""Actionable Meta Marketing API errors (never include access tokens)."""

from __future__ import annotations

from typing import Any


class MetaApiError(RuntimeError):
    """Base Meta Graph / Marketing API failure."""

    code: str = "META_API_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        meta_code: int | None = None,
        meta_subcode: int | None = None,
        meta_type: str | None = None,
        fbtrace_id: str | None = None,
    ):
        self.code = code or self.code
        self.http_status = http_status
        self.meta_code = meta_code
        self.meta_subcode = meta_subcode
        self.meta_type = meta_type
        self.fbtrace_id = fbtrace_id
        super().__init__(f"{self.code}: {message}")


class MetaTokenMissing(MetaApiError):
    code = "META_TOKEN_MISSING"


class MetaTokenExpired(MetaApiError):
    code = "META_TOKEN_EXPIRED"


class MetaPermissionDenied(MetaApiError):
    code = "META_PERMISSION_DENIED"


class MetaRateLimited(MetaApiError):
    code = "META_RATE_LIMITED"


class MetaInvalidAdAccount(MetaApiError):
    code = "META_INVALID_AD_ACCOUNT"


class MetaUnsupportedField(MetaApiError):
    code = "META_UNSUPPORTED_FIELD"


class MetaNetworkError(MetaApiError):
    code = "META_NETWORK_ERROR"


class MetaServerError(MetaApiError):
    code = "META_SERVER_ERROR"


class MetaWriteDisabled(MetaApiError):
    code = "META_WRITE_DISABLED"


class MetaActiveDisabled(MetaApiError):
    code = "META_ACTIVE_DISABLED"


class MetaBudgetPolicyError(MetaApiError):
    code = "META_BUDGET_POLICY"


class MetaApprovalRequired(MetaApiError):
    code = "META_APPROVAL_REQUIRED"


class MetaSafetyError(MetaApiError):
    code = "META_SAFETY_ERROR"


def _safe_error_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    err = payload.get("error")
    return err if isinstance(err, dict) else {}


def classify_meta_error(
    *,
    http_status: int | None,
    payload: Any = None,
    network_message: str | None = None,
) -> MetaApiError:
    """Map HTTP / Meta error JSON to an actionable exception (no secrets)."""
    if network_message and http_status is None:
        return MetaNetworkError(network_message)

    err = _safe_error_payload(payload)
    message = str(err.get("message") or payload or "Meta API request failed")
    meta_type = str(err.get("type") or "") or None
    meta_code = err.get("code")
    meta_subcode = err.get("error_subcode")
    fbtrace_id = err.get("fbtrace_id")
    try:
        meta_code_i = int(meta_code) if meta_code is not None else None
    except (TypeError, ValueError):
        meta_code_i = None
    try:
        meta_subcode_i = int(meta_subcode) if meta_subcode is not None else None
    except (TypeError, ValueError):
        meta_subcode_i = None

    lower = message.lower()
    kwargs = dict(
        http_status=http_status,
        meta_code=meta_code_i,
        meta_subcode=meta_subcode_i,
        meta_type=meta_type,
        fbtrace_id=str(fbtrace_id) if fbtrace_id else None,
    )

    # Token / OAuth
    if http_status == 401 or meta_code_i in {190, 102} or "session has expired" in lower:
        return MetaTokenExpired(
            "access token invalid or expired — refresh META_ACCESS_TOKEN",
            **kwargs,
        )
    if meta_type == "OAuthException" and meta_code_i in {190, 102, 463, 467}:
        return MetaTokenExpired(
            "OAuthException — token expired or revoked",
            **kwargs,
        )

    # Permissions
    if http_status == 403 or meta_code_i in {10, 200, 294}:
        if meta_subcode_i == 1487194:
            return MetaPermissionDenied(
                "META_PERMISSION_DENIED: cannot use Page object in Ad Creative "
                "(subcode 1487194). Likely missing pages_manage_ads / Page advertiser "
                "role, or post not visible to this token for ads use",
                **kwargs,
            )
        hint = "ads_read / ads_management permission missing or insufficient"
        if "ads_management" in lower:
            hint = "ads_management missing"
        elif "ads_read" in lower:
            hint = "ads_read missing"
        elif "pages_manage_ads" in lower:
            hint = "pages_manage_ads missing"
        return MetaPermissionDenied(f"{hint}: {message}", **kwargs)
    if "permission" in lower or "(#200)" in message:
        return MetaPermissionDenied(f"ads_read/ads_management likely missing: {message}", **kwargs)

    # Rate limit
    if http_status == 429 or meta_code_i in {4, 17, 32, 613} or "rate limit" in lower:
        return MetaRateLimited(
            "Meta rate limit hit — backoff and retry later",
            **kwargs,
        )

    # Invalid ad account
    if meta_code_i in {100, 803} and ("ad account" in lower or "act_" in lower):
        return MetaInvalidAdAccount(message, **kwargs)
    if "unsupported get request" in lower and "act_" in lower:
        return MetaInvalidAdAccount(message, **kwargs)

    # Unsupported field
    if "nonexisting field" in lower or "unknown field" in lower or "invalid field" in lower:
        return MetaUnsupportedField(message, **kwargs)

    # Transient server
    if http_status is not None and http_status >= 500:
        return MetaServerError(f"Meta 5xx ({http_status}): {message}", **kwargs)

    if meta_type == "OAuthException":
        return MetaPermissionDenied(f"OAuthException: {message}", **kwargs)

    return MetaApiError(message, **kwargs)
