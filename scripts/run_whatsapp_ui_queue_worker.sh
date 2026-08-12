#!/bin/bash
# One-shot WhatsApp list sync queue processor.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONUNBUFFERED=1
export PYTHONPATH="${ROOT}/whatsapp_ui_sync/src${PYTHONPATH:+:$PYTHONPATH}"
export WHATSAPP_UI_CDP_URL="${WHATSAPP_UI_CDP_URL:-http://127.0.0.1:9222}"
exec python3 "$ROOT/scripts/whatsapp_ui_queue_worker.py"
