"""HTTP client for amoCRM Chat API (amojo) — no live calls unless explicitly invoked."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from agent7_envoy.amo_chat.config import AmoChatChannelConfig, AmoChatConfig
from agent7_envoy.amo_chat.signing import sign_request


HttpTransport = Callable[[str, str, dict[str, str], bytes, float], tuple[int, bytes]]


def _default_transport(
    method: str, url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body if method.upper() != "GET" else None, method=method.upper())
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read() or b""


@dataclass
class AmojoResponse:
    ok: bool
    status_code: int
    data: dict[str, Any]
    raw: bytes
    error: str = ""


class AmojoChatClient:
    """Signed Chat API client. Secrets never logged."""

    def __init__(
        self,
        config: AmoChatConfig,
        *,
        transport: HttpTransport | None = None,
        timeout: float = 20.0,
        dry_run: bool = True,
    ):
        self.config = config
        self._transport = transport or _default_transport
        self.timeout = timeout
        self.dry_run = dry_run

    def _request(
        self,
        *,
        method: str,
        path: str,
        channel: AmoChatChannelConfig,
        payload: dict[str, Any] | None = None,
    ) -> AmojoResponse:
        body_obj = payload if payload is not None else {}
        body = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        headers = sign_request(
            method=method,
            body=body,
            path=path,
            secret=channel.channel_secret,
        )
        url = f"{self.config.amojo_base_url}{path}"
        if self.dry_run:
            return AmojoResponse(
                ok=True,
                status_code=0,
                data={"dry_run": True, "path": path, "payload": body_obj},
                raw=b"",
                error="",
            )
        status, raw = self._transport(method, url, headers, body, self.timeout)
        data: dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = {"raw_text": raw.decode("utf-8", errors="replace")[:500]}
        ok = 200 <= status < 300
        return AmojoResponse(
            ok=ok,
            status_code=status,
            data=data,
            raw=raw,
            error="" if ok else f"http_{status}",
        )

    def connect_channel(
        self,
        channel: AmoChatChannelConfig,
        *,
        account_id: str | None = None,
        title: str | None = None,
    ) -> AmojoResponse:
        if not channel.configured:
            return AmojoResponse(
                ok=False,
                status_code=0,
                data={},
                raw=b"",
                error="channel_not_configured",
            )
        acct = account_id or channel.account_id or self.config.account_id
        if not acct:
            return AmojoResponse(
                ok=False, status_code=0, data={}, raw=b"", error="account_id_missing"
            )
        path = f"/v2/origin/custom/{channel.channel_id}/connect"
        return self._request(
            method="POST",
            path=path,
            channel=channel,
            payload={
                "account_id": acct,
                "title": title or channel.title,
                "hook_api_version": "v2",
            },
        )

    def import_message(
        self,
        channel: AmoChatChannelConfig,
        payload: dict[str, Any],
    ) -> AmojoResponse:
        if not channel.connected:
            return AmojoResponse(
                ok=False, status_code=0, data={}, raw=b"", error="scope_not_connected"
            )
        path = f"/v2/origin/custom/{channel.scope_id}"
        body = {"event_type": "new_message", "payload": payload}
        return self._request(method="POST", path=path, channel=channel, payload=body)

    def set_delivery_status(
        self,
        channel: AmoChatChannelConfig,
        msgid: str,
        *,
        delivery_status: int,
        error_code: str | None = None,
        error: str | None = None,
    ) -> AmojoResponse:
        """Official: POST /v2/origin/custom/{scope_id}/{msgid}/delivery_status

        delivery_status codes (amo docs): typically 0=sent, errors via error fields.
        """
        if not channel.connected:
            return AmojoResponse(
                ok=False, status_code=0, data={}, raw=b"", error="scope_not_connected"
            )
        path = f"/v2/origin/custom/{channel.scope_id}/{msgid}/delivery_status"
        payload: dict[str, Any] = {"delivery_status": int(delivery_status)}
        if error_code:
            payload["error_code"] = error_code
        if error:
            payload["error"] = error
        return self._request(method="POST", path=path, channel=channel, payload=payload)
