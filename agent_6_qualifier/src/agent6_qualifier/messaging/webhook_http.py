"""Minimal HTTP webhook route for Wazzup (stdlib). Disabled unless WAZZUP_WEBHOOK_ENABLED.

Route: POST /webhooks/wazzup

Returns 200 after validation + shared Agent 6 processing.
Live WhatsApp POST is gated by send/auto_reply/allowlist — not by refusing to serve.
Never logs Authorization / API keys.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent6_qualifier.messaging.idempotency import ProcessedEventStore
from agent6_qualifier.messaging.wazzup_config import load_wazzup_config
from agent6_qualifier.messaging.wazzup_errors import (
    WazzupMalformedPayload,
    WazzupWebhookDisabled,
)
from agent6_qualifier.messaging.inbound_debounce import InboundDebounceBuffer
from agent6_qualifier.messaging.webhook import (
    MAX_BODY_BYTES,
    flush_debounced_client_turns,
    process_wazzup_webhook,
)

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhooks/wazzup"
HEALTH_PATH = "/health"
DEFAULT_PORT = 8765


def handle_wazzup_webhook_request(
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    store: ProcessedEventStore | None = None,
    run_qualification_dry_run: bool = True,
    amo: Any | None = None,
    qualifier: Any | None = None,
) -> tuple[int, dict[str, Any]]:
    """Pure request handler (testable without binding a port)."""
    parsed = urlparse(path)
    clean = parsed.path.rstrip("/") or "/"
    if clean == HEALTH_PATH.rstrip("/") or clean == f"{WEBHOOK_PATH.rstrip('/')}/health":
        if method.upper() not in {"GET", "POST"}:
            return 405, {"ok": False, "error": "method_not_allowed"}
        return 200, {"ok": True, "health": True, "service": "wazzup_webhook"}
    if clean != WEBHOOK_PATH.rstrip("/"):
        return 404, {"ok": False, "error": "not_found"}
    if method.upper() != "POST":
        return 405, {"ok": False, "error": "method_not_allowed"}

    cfg = load_wazzup_config()
    try:
        result = process_wazzup_webhook(
            body,
            config=cfg,
            store=store,
            headers=headers,
            run_qualification_dry_run=run_qualification_dry_run,
            amo=amo,
            qualifier=qualifier,
            debounce=getattr(WazzupWebhookHandler, "debounce", None),
            defer_client_turns=getattr(
                WazzupWebhookHandler, "defer_client_turns", False
            ),
        )
    except WazzupWebhookDisabled as exc:
        return 503, {"ok": False, "error": exc.code, "message": str(exc)}
    except PermissionError:
        return 401, {"ok": False, "error": "WAZZUP_WEBHOOK_AUTH_FAILED"}
    except WazzupMalformedPayload as exc:
        return 400, {"ok": False, "error": exc.code, "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("wazzup webhook failed: %s", type(exc).__name__)
        return 500, {"ok": False, "error": "WAZZUP_WEBHOOK_ERROR"}

    if result.is_test_ping:
        return 200, {"ok": True, "test": True}

    return 200, {
        "ok": True,
        "accepted": result.accepted,
        "messages": len(result.messages),
        "duplicates": len(result.duplicates),
        "dry_runs": len(result.dry_runs),
        "turns": len(result.turns),
        "auto_reply": result.auto_reply,
        "send": bool(cfg.send_enabled),
        "ignored_reason": result.ignored_reason,
    }


class WazzupWebhookHandler(BaseHTTPRequestHandler):
    store: ProcessedEventStore | None = None
    run_qualification_dry_run: bool = True
    amo: Any | None = None
    qualifier: Any | None = None

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.info("wazzup_webhook %s", fmt % args)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._json(413, {"ok": False, "error": "body_too_large"})
            return
        body = self.rfile.read(length) if length else b""
        headers = {k: v for k, v in self.headers.items()}
        status, payload = handle_wazzup_webhook_request(
            method="POST",
            path=self.path,
            headers=headers,
            body=body,
            store=self.store,
            run_qualification_dry_run=self.run_qualification_dry_run,
            amo=self.amo,
            qualifier=self.qualifier,
        )
        # Always flush a compact safe line for controlled live tests.
        print(
            f"[wazzup_webhook] status={status} body_keys={sorted(payload.keys())} "
            f"messages={payload.get('messages')} dry_runs={payload.get('dry_runs')} "
            f"duplicates={payload.get('duplicates')} test={payload.get('test')}",
            flush=True,
        )
        self._json(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        status, payload = handle_wazzup_webhook_request(
            method="GET",
            path=self.path,
            headers={k: v for k, v in self.headers.items()},
            body=b"",
            store=self.store,
        )
        self._json(status, payload)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve_wazzup_webhook(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    *,
    store_path: Path | str | None = None,
) -> None:
    """Bind local webhook server. Requires WAZZUP_WEBHOOK_ENABLED=true.

    Live send is optional and gated by WAZZUP_SEND_ENABLED +
    WAZZUP_AUTO_REPLY_ENABLED + WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF +
    allowlist (see WazzupConfig / outbound_guard / bot_may_send).
    """
    cfg = load_wazzup_config()
    if not cfg.webhook_enabled:
        raise WazzupWebhookDisabled("WAZZUP_WEBHOOK_ENABLED=false")
    path = Path(store_path) if store_path else Path("data/wazzup_processed.sqlite")
    WazzupWebhookHandler.store = ProcessedEventStore(path)
    WazzupWebhookHandler.run_qualification_dry_run = True
    debounce = None
    if cfg.debounce_sec > 0:
        debounce = InboundDebounceBuffer(window_sec=cfg.debounce_sec)
        WazzupWebhookHandler.debounce = debounce
        WazzupWebhookHandler.defer_client_turns = True

        def _flush_loop() -> None:
            while True:
                wait = debounce.min_remaining()
                if wait is None:
                    time.sleep(min(1.0, cfg.debounce_sec))
                    continue
                if wait > 0:
                    time.sleep(wait)
                flush_debounced_client_turns(
                    debounce,
                    config=cfg,
                    store=WazzupWebhookHandler.store,
                    amo=WazzupWebhookHandler.amo,
                    qualifier=WazzupWebhookHandler.qualifier,
                )

        threading.Thread(target=_flush_loop, name="wa-debounce", daemon=True).start()
    server = HTTPServer((host, port), WazzupWebhookHandler)
    logger.info(
        "Wazzup webhook listening on http://%s:%s%s "
        "(send=%s auto_reply=%s autoresponse_confirmed_off=%s allowlist=%s)",
        host,
        port,
        WEBHOOK_PATH,
        cfg.send_enabled,
        cfg.auto_reply_enabled,
        cfg.external_autoresponse_confirmed_off,
        cfg.live_allowlist_enabled,
    )
    print(
        f"Listening http://{host}:{port}{WEBHOOK_PATH}\n"
        f"health http://{host}:{port}{HEALTH_PATH}\n"
        f"send_enabled={cfg.send_enabled} auto_reply_enabled={cfg.auto_reply_enabled}\n"
        f"external_autoresponse_confirmed_off="
        f"{cfg.external_autoresponse_confirmed_off}\n"
        f"live_allowlist_enabled={cfg.live_allowlist_enabled} "
        f"allowlist_count={len(cfg.live_allowlist_phones)}\n"
        "Public HTTPS: NOT provided by this process (localhost only)."
    )
    if cfg.send_enabled and cfg.auto_reply_enabled and not cfg.live_allowlist_enabled:
        if cfg.stage_mode:
            print(
                "WARNING: STAGE_MODE live without allowlist — "
                "outbound_guard will block; set allowlist or STAGE_MODE=false"
            )
        else:
            print(
                "NOTICE: production-like live (allowlist OFF, stage_mode OFF)"
            )
    server.serve_forever()
