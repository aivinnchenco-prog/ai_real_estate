"""Actionable Wazzup API errors (never include API keys)."""

from __future__ import annotations

from typing import Any


class WazzupError(RuntimeError):
    code: str = "WAZZUP_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
    ):
        self.code = code or self.code
        self.http_status = http_status
        super().__init__(f"{self.code}: {message}")


class WazzupTokenMissing(WazzupError):
    code = "WAZZUP_TOKEN_MISSING"


class WazzupTokenInvalid(WazzupError):
    code = "WAZZUP_TOKEN_INVALID"


class WazzupChannelNotFound(WazzupError):
    code = "WAZZUP_CHANNEL_NOT_FOUND"


class WazzupRateLimited(WazzupError):
    code = "WAZZUP_RATE_LIMITED"


class WazzupSendDisabled(WazzupError):
    code = "WAZZUP_SEND_DISABLED"


class WazzupAutoReplyDisabled(WazzupError):
    code = "WAZZUP_AUTO_REPLY_DISABLED"


class WazzupExternalAutoresponseNotConfirmed(WazzupError):
    """Live POST blocked until user confirms WA/Wazzup/amo greetings are OFF."""

    code = "EXTERNAL_AUTORESPONSE_NOT_CONFIRMED_OFF"


class WazzupWebhookDisabled(WazzupError):
    code = "WAZZUP_WEBHOOK_DISABLED"


class WazzupNetworkError(WazzupError):
    code = "WAZZUP_NETWORK_ERROR"


class WazzupServerError(WazzupError):
    code = "WAZZUP_SERVER_ERROR"


class WazzupMalformedPayload(WazzupError):
    code = "WAZZUP_MALFORMED_PAYLOAD"


def classify_wazzup_error(
    *,
    http_status: int | None,
    payload: Any = None,
    network_message: str | None = None,
) -> WazzupError:
    if network_message and http_status is None:
        return WazzupNetworkError(network_message)

    message = "Wazzup API request failed"
    if isinstance(payload, dict):
        message = str(
            payload.get("message")
            or payload.get("error")
            or payload.get("detail")
            or message
        )
    elif payload is not None:
        message = str(payload)[:500]

    if http_status == 401:
        return WazzupTokenInvalid("invalid API key / unauthorized", http_status=401)
    if http_status == 403:
        return WazzupTokenInvalid(f"forbidden: {message}", http_status=403)
    if http_status == 404:
        return WazzupChannelNotFound(message, http_status=404)
    if http_status == 429:
        return WazzupRateLimited("rate limit — backoff and retry later", http_status=429)
    if http_status is not None and http_status >= 500:
        return WazzupServerError(f"Wazzup 5xx ({http_status}): {message}", http_status=http_status)
    return WazzupError(message, http_status=http_status)
