"""HTTP server factory for amoCRM Chat API webhooks (local serve + tests)."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse

from agent7_envoy.amo_chat.events import amo_chat_event
from agent7_envoy.amo_chat.production import MAX_WEBHOOK_BODY_BYTES
from agent7_envoy.amo_chat.routing import resolve_webhook_channel
from agent7_envoy.amo_chat.webhook import AmoChatWebhookHandler


def build_request_handler(handler: AmoChatWebhookHandler) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802
            # Path/status only — never log bodies/headers (may contain secrets).
            sys.stderr.write("[amo-chat-webhook] " + (fmt % args) + "\n")

        def _send(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if urlparse(self.path).path.rstrip("/") in {
                "/health",
                "/webhooks/amo-chat/health",
            }:
                self._send(
                    200,
                    {
                        "status": "ok",
                        "ok": True,
                        "service": "openhome_api",
                    },
                )
                return
            self._send(404, {"ok": False, "status": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            clean = urlparse(self.path).path.rstrip("/")
            if not clean.startswith("/webhooks/amo-chat"):
                self._send(404, {"ok": False, "error": "not_found"})
                return
            route = resolve_webhook_channel(self.path, handler.config)
            if not route.ok or not route.channel_key:
                amo_chat_event(
                    "AMO_CHAT_UNKNOWN_SCOPE",
                    scope_id=route.scope_id,
                    reason=route.reason or "unknown_scope",
                )
                self._send(
                    404,
                    {
                        "ok": False,
                        "error": route.reason or "unknown_scope",
                        "scope_id": route.scope_id,
                    },
                )
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send(400, {"ok": False, "error": "invalid_content_length"})
                return
            if length < 0 or length > MAX_WEBHOOK_BODY_BYTES:
                self._send(413, {"ok": False, "error": "body_too_large"})
                return
            body = self.rfile.read(length) if length else b"{}"
            headers = {k: v for k, v in self.headers.items()}
            try:
                result = handler.handle(
                    channel_key=route.channel_key,
                    body=body,
                    headers=headers,
                    path=clean,
                )
            except json.JSONDecodeError:
                self._send(400, {"ok": False, "error": "malformed_json"})
                return
            except Exception as exc:
                # Keep process alive; do not include request body or secrets.
                self._send(
                    500,
                    {
                        "ok": False,
                        "error": "handler_error",
                        "type": type(exc).__name__,
                    },
                )
                return
            self._send(
                200 if result.ok or result.action == "ignored" else 502,
                {
                    "ok": result.ok,
                    "action": result.action,
                    "reason": result.reason,
                    "origin": result.origin,
                    "channel": route.channel_key,
                },
            )

    return H
