#!/bin/bash
# Keep NORMAL Google Chrome + CDP up for WhatsApp Lists sync (VPS).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONUNBUFFERED=1
export PYTHONPATH="${ROOT}/whatsapp_ui_sync/src${PYTHONPATH:+:$PYTHONPATH}"
# Prefer CDP attach mode for workers
export WHATSAPP_UI_CDP_URL="${WHATSAPP_UI_CDP_URL:-http://127.0.0.1:9222}"
export WHATSAPP_UI_CDP_PORT="${WHATSAPP_UI_CDP_PORT:-9222}"
# On headless VPS use Xvfb unless DISPLAY already set
export WHATSAPP_UI_USE_XVFB="${WHATSAPP_UI_USE_XVFB:-true}"
exec python3 "$ROOT/scripts/whatsapp_ui_open_native_chrome.py"
