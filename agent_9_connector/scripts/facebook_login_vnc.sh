#!/bin/bash
# Facebook login через noVNC: Chromium на VPS, профиль Agent 9 (facebook_agent9).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export AGENT9_FACEBOOK_PROFILE_DIR="${AGENT9_FACEBOOK_PROFILE_DIR:-/opt/openhome/runtime/browser_profiles/facebook_agent9}"
export PYTHONPATH="${PYTHONPATH:-$ROOT/src}"

_load_env() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" != *=* ]] && continue
    local key="${line%%=*}"
    key="${key#"${key%%[![:space:]]*}"}"
    local val="${line#*=}"
    case "$key" in
      AGENT9_*|ERROR_BOT_TOKEN|ERROR_CHAT_ID)
        export "$key=$val"
        ;;
    esac
  done < "$f"
}

_load_env /opt/openhome/.env
export DISPLAY="${DISPLAY:-:109}"
export AGENT9_VNC_PORT="${AGENT9_VNC_PORT:-5900}"
export AGENT9_WEB_PORT="${AGENT9_WEB_PORT:-6080}"
PY="${PY:-/opt/openhome/venv/bin/python3}"

log() { echo "[agent9-vnc-login] $*"; }

cleanup() {
  log "cleanup..."
  [[ -n "${LOGIN_PID:-}" ]] && kill "$LOGIN_PID" 2>/dev/null || true
  [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null || true
  [[ -n "${VNC_PID:-}" ]] && kill "$VNC_PID" 2>/dev/null || true
  [[ -n "${XVFB_PID:-}" ]] && kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

log "Stopping agent9 if running..."
systemctl stop openhome-agent9.service 2>/dev/null || true
systemctl stop openhome-agent9-connector.service 2>/dev/null || true
pkill -f facebook_login_remote.py 2>/dev/null || true

RUN_AS="${AGENT9_LOGIN_USER:-openhome}"

if ! command -v Xvfb >/dev/null; then
  log "Installing xvfb x11vnc novnc websockify..."
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xvfb x11vnc novnc websockify
fi

mkdir -p "$AGENT9_FACEBOOK_PROFILE_DIR"
chown "$RUN_AS:$RUN_AS" "$AGENT9_FACEBOOK_PROFILE_DIR" 2>/dev/null || true

log "Starting virtual display $DISPLAY"
Xvfb "$DISPLAY" -screen 0 1280x900x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 2

log "Starting x11vnc on port $AGENT9_VNC_PORT"
x11vnc -display "$DISPLAY" -nopw -listen 127.0.0.1 -rfbport "$AGENT9_VNC_PORT" -forever -shared -bg -o /tmp/agent9_x11vnc.log
sleep 1

NOVNC_WEB="/usr/share/novnc"
if [[ ! -d "$NOVNC_WEB" ]]; then
  NOVNC_WEB="$(dirname "/usr/share/novnc/vnc.html")"
fi

log "Starting noVNC web on port $AGENT9_WEB_PORT"
websockify --web="$NOVNC_WEB" "$AGENT9_WEB_PORT" "127.0.0.1:$AGENT9_VNC_PORT" &
WEB_PID=$!
sleep 1

log "Profile: $AGENT9_FACEBOOK_PROFILE_DIR"
log "Legacy backup (не трогаем): ${AGENT9_DATA_DIR:-/opt/openhome/runtime/state/agent9}/facebook_profile"
log ""
log "============================================================"
log "Откройте в браузере на Mac (после SSH tunnel):"
log "  http://localhost:${AGENT9_WEB_PORT}/vnc.html?autoconnect=true"
log ""
log "SSH tunnel с Mac:"
log "  ssh -L ${AGENT9_WEB_PORT}:127.0.0.1:${AGENT9_WEB_PORT} USER@VPS"
log "============================================================"
log ""

export AGENT9_FB_LOGIN_TIMEOUT_SEC="${AGENT9_FB_LOGIN_TIMEOUT_SEC:-1800}"
sudo -u "$RUN_AS" env AGENT9_FACEBOOK_PROFILE_DIR="$AGENT9_FACEBOOK_PROFILE_DIR" \
  PYTHONPATH="$PYTHONPATH" DISPLAY="$DISPLAY" \
  AGENT9_FB_LOGIN_TIMEOUT_SEC="$AGENT9_FB_LOGIN_TIMEOUT_SEC" \
  "$PY" - <<'PY' &
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("PYTHONPATH", "src"))
from agent9_connector.connectors.facebook import FacebookConnector
from agent9_connector.profile_lock import facebook_profile_lock
from agent9_connector.config_loader import facebook_profile_dir

VERIFY = os.environ.get(
    "AGENT9_FB_VERIFY_URL",
    "https://www.facebook.com/marketplace/item/1515735863510647",
)
timeout = int(os.environ.get("AGENT9_FB_LOGIN_TIMEOUT_SEC", "1800"))
profile = facebook_profile_dir()

with facebook_profile_lock(profile):
    fb = FacebookConnector(headless=False, mock_mode=False)
    fb.start_browser()
    page = fb._page
    page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
    print(f"[login] Chromium открыт — войдите через noVNC (profile={profile})", flush=True)

    deadline = time.time() + timeout
    ok = False
    while time.time() < deadline:
        names = {c["name"] for c in fb._browser.cookies()}
        if "c_user" in names:
            page.goto(VERIFY, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(3000)
            if "login" not in page.url.lower():
                ok = True
                print("[login] SUCCESS", flush=True)
                break
        time.sleep(5)

    fb.stop_browser()
sys.exit(0 if ok else 1)
PY
LOGIN_PID=$!

wait "$LOGIN_PID"
EXIT=$?
if [[ "$EXIT" -eq 0 ]]; then
  chown -R "$RUN_AS:$RUN_AS" "$AGENT9_FACEBOOK_PROFILE_DIR" 2>/dev/null || true
  log "Login OK — cookies saved. Starting agent9..."
  systemctl start openhome-agent9.service 2>/dev/null || true
  systemctl start openhome-agent9-connector.service 2>/dev/null || true
else
  log "Login не завершён (timeout или ошибка). Agent9 не запущен."
fi
exit "$EXIT"
