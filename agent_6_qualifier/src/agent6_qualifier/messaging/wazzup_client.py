"""Wazzup HTTP client (api.wazzup24.com v3). Never logs API key / Authorization."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agent6_qualifier.messaging.wazzup_config import WazzupConfig, load_wazzup_config
from agent6_qualifier.messaging.wazzup_errors import (
    WazzupError,
    WazzupSendDisabled,
    WazzupServerError,
    WazzupTokenMissing,
    classify_wazzup_error,
)

logger = logging.getLogger(__name__)

HttpTransport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]


def _default_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes]:
    req = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status), resp.read()
    except HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        return int(exc.code), raw or b"{}"
    except URLError as exc:
        raise classify_wazzup_error(
            http_status=None, network_message=str(exc.reason or exc)
        ) from exc


def new_crm_message_id(*, prefix: str = "agent6") -> str:
    """Deterministic-unique outbound idempotency key for Wazzup crmMessageId."""
    return f"{prefix}-{uuid.uuid4()}"


class WazzupClient:
    """Minimal confirmed endpoints: GET /v3/channels, POST /v3/message."""

    def __init__(
        self,
        config: WazzupConfig | None = None,
        *,
        transport: HttpTransport | None = None,
    ):
        self.config = config or load_wazzup_config()
        self._transport = transport or _default_transport
        self.network_calls = 0
        self.request_log: list[dict[str, Any]] = []

    def _ensure_key(self) -> None:
        if not self.config.api_key_set:
            raise WazzupTokenMissing("WAZZUP_API_KEY is missing — set it in local .env")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
            "User-Agent": "Agent6-WazzupClient/1.0",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        allow_retry: bool | None = None,
    ) -> Any:
        self._ensure_key()
        method_u = method.upper()
        if method_u in {"POST", "PATCH", "PUT", "DELETE"}:
            max_attempts = 1  # no blind retry for mutating calls
        else:
            max_attempts = (
                1 + max(0, int(self.config.get_max_retries))
                if (allow_retry is None or allow_retry)
                else 1
            )

        path_clean = path if path.startswith("/") else f"/{path}"
        url = f"{self.config.api_base_url}{path_clean}"
        body = None if json_body is None else json.dumps(json_body).encode("utf-8")
        headers = self._headers()

        self.request_log.append(
            {
                "method": method_u,
                "path": path_clean,
                "has_body": json_body is not None,
                # never store Authorization
            }
        )
        logger.debug("Wazzup API %s %s", method_u, path_clean)

        last_error: WazzupError | None = None
        for attempt in range(max_attempts):
            self.network_calls += 1
            try:
                status, raw = self._transport(
                    method_u,
                    url,
                    headers,
                    body,
                    float(self.config.request_timeout_seconds),
                )
            except WazzupError as exc:
                last_error = exc
                if attempt + 1 < max_attempts and isinstance(exc, (WazzupServerError,)):
                    time.sleep(0.05 * (attempt + 1))
                    continue
                from agent6_qualifier.messaging.wazzup_errors import WazzupNetworkError

                if attempt + 1 < max_attempts and isinstance(exc, WazzupNetworkError):
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise

            try:
                payload = json.loads(raw.decode("utf-8") or "null")
            except json.JSONDecodeError:
                payload = {"message": raw.decode("utf-8", errors="replace")[:500]}

            if status >= 500:
                err = classify_wazzup_error(http_status=status, payload=payload)
                last_error = err
                if attempt + 1 < max_attempts:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise err
            if status >= 400:
                raise classify_wazzup_error(http_status=status, payload=payload)
            return payload

        assert last_error is not None
        raise last_error

    def get_channels(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/v3/channels")
        if isinstance(payload, list):
            return [c for c in payload if isinstance(c, dict)]
        if isinstance(payload, dict):
            data = payload.get("data") or payload.get("channels") or []
            if isinstance(data, list):
                return [c for c in data if isinstance(c, dict)]
        return []

    def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        cid = str(channel_id).strip()
        for ch in self.get_channels():
            if str(ch.get("channelId") or ch.get("id") or "") == cid:
                return normalize_channel(ch)
        return None

    def get_webhooks(self) -> dict[str, Any] | list[Any] | None:
        """GET /v3/webhooks (read-only)."""
        return self._request("GET", "/v3/webhooks")

    def set_webhooks_uri(self, webhooks_uri: str) -> Any:
        """PATCH /v3/webhooks — URI + required subscriptions. Never message send."""
        uri = (webhooks_uri or "").strip()
        if not uri.startswith("https://"):
            raise WazzupError(
                "webhooksUri must be https://", code="WAZZUP_WEBHOOK_URI_INVALID"
            )
        return self._request(
            "PATCH",
            "/v3/webhooks",
            json_body={
                "webhooksUri": uri,
                "subscriptions": {
                    "messagesAndStatuses": True,
                    "contactsAndDealsCreation": False,
                    "channelsUpdates": True,
                    "templateStatus": False,
                },
            },
            allow_retry=False,
        )

    def healthcheck(self) -> dict[str, Any]:
        channels = [normalize_channel(c) for c in self.get_channels()]
        expected = next(
            (c for c in channels if c.get("channel_id") == self.config.channel_id),
            None,
        )
        return {
            "ok": expected is not None,
            "api": "CONNECTED",
            "channel": expected,
            "channels_count": len(channels),
        }

    def send_text(
        self,
        *,
        chat_id: str,
        text: str,
        crm_message_id: str,
        channel_id: str | None = None,
        chat_type: str = "whatsapp",
    ) -> dict[str, Any]:
        if not self.config.send_enabled:
            raise WazzupSendDisabled(
                "WAZZUP_SEND_ENABLED=false — POST /v3/message blocked before network"
            )
        payload = {
            "channelId": channel_id or self.config.channel_id,
            "chatType": chat_type,
            "chatId": chat_id,
            "text": text,
            "crmMessageId": crm_message_id,
        }
        return self._request("POST", "/v3/message", json_body=payload, allow_retry=False)

    def send_document(
        self,
        *,
        chat_id: str,
        content_uri: str,
        crm_message_id: str,
        channel_id: str | None = None,
        chat_type: str = "whatsapp",
    ) -> dict[str, Any]:
        """POST /v3/message with contentUri only (Wazzup: text XOR contentUri)."""
        if not self.config.send_enabled:
            raise WazzupSendDisabled(
                "WAZZUP_SEND_ENABLED=false — POST /v3/message blocked before network"
            )
        uri = (content_uri or "").strip()
        if not uri.startswith("https://"):
            raise WazzupError(
                "contentUri must be https public URL",
                code="WAZZUP_CONTENT_URI_INVALID",
            )
        payload = {
            "channelId": channel_id or self.config.channel_id,
            "chatType": chat_type,
            "chatId": chat_id,
            "contentUri": uri,
            "crmMessageId": crm_message_id,
        }
        return self._request("POST", "/v3/message", json_body=payload, allow_retry=False)


def normalize_channel(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel_id": str(raw.get("channelId") or raw.get("id") or ""),
        "transport": str(raw.get("transport") or raw.get("chatType") or "").lower() or None,
        "state": str(raw.get("state") or raw.get("status") or "").lower() or None,
        "plain_id": str(raw.get("plainId") or raw.get("plain_id") or "") or None,
        "name": raw.get("name"),
        "raw_keys": sorted(raw.keys()),
    }
