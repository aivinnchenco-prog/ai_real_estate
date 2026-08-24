#!/bin/bash
# Agent 7 Facebook login через noVNC (owner communication profile).
# НЕ запускать пока не остановлены сервисы, использующие facebook_agent7.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ROOT="$(cd "$ROOT/.." && pwd)"
cd "$APP_ROOT"

export AGENT7_FACEBOOK_PROFILE_DIR="${AGENT7_FACEBOOK_PROFILE_DIR:-/opt/openhome/runtime/browser_profiles/facebook_agent7}"
export PYTHONPATH="${PYTHONPATH:-$APP_ROOT:$APP_ROOT/agent_6_qualifier/src}"
export DISPLAY="${DISPLAY:-:110}"

export AGENT7_VNC_PORT="${AGENT7_VNC_PORT:-5901}"
export AGENT7_WEB_PORT="${AGENT7_WEB_PORT:-6081}"
PY="${PY:-/opt/openhome/venv/bin/python3}"
RUN_AS="${AGENT7_LOGIN_USER:-openhome}"

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
      AGENT7_*|ERROR_BOT_TOKEN|ERROR_CHAT_ID)
        export "$key=$val"
        ;;
    esac
  done < "$f"
}

log() { echo "[agent7-vnc-login] $*"; }

cleanup() {
  log "cleanup..."
  [[ -n "${LOGIN_PID:-}" ]] && kill "$LOGIN_PID" 2>/dev/null || true
  [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null || true
  [[ -n "${VNC_PID:-}" ]] && kill "$VNC_PID" 2>/dev/null || true
  [[ -n "${XVFB_PID:-}" ]] && kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

_load_env /opt/openhome/.env

log "Stopping services that may use Agent7 FB profile..."
systemctl stop openhome-agent7-userbot.service 2>/dev/null || true
pkill -f facebook_agent7_login.py 2>/dev/null || true

if ! command -v Xvfb >/dev/null; then
  log "Installing xvfb x11vnc novnc websockify..."
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xvfb x11vnc novnc websockify
fi

mkdir -p "$AGENT7_FACEBOOK_PROFILE_DIR"
chown "$RUN_AS:$RUN_AS" "$AGENT7_FACEBOOK_PROFILE_DIR" 2>/dev/null || true

log "Starting virtual display $DISPLAY"
Xvfb "$DISPLAY" -screen 0 1280x900x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 2

log "Starting x11vnc on port $AGENT7_VNC_PORT"
x11vnc -display "$DISPLAY" -nopw -listen 127.0.0.1 -rfbport "$AGENT7_VNC_PORT" -forever -shared -bg -o /tmp/agent7_x11vnc.log
sleep 1

NOVNC_WEB="/usr/share/novnc"
if [[ ! -d "$NOVNC_WEB" ]]; then
  NOVNC_WEB="$(dirname "/usr/share/novnc/vnc.html")"
fi

log "Starting noVNC web on port $AGENT7_WEB_PORT"
websockify --web="$NOVNC_WEB" "$AGENT7_WEB_PORT" "127.0.0.1:$AGENT7_VNC_PORT" &
WEB_PID=$!
sleep 1

log "Profile: $AGENT7_FACEBOOK_PROFILE_DIR"
log ""
log "============================================================"
log "Откройте в браузере на Mac (после SSH tunnel):"
log "  http://localhost:${AGENT7_WEB_PORT}/vnc.html?autoconnect=true"
log ""
log "SSH tunnel с Mac:"
log "  ssh -L ${AGENT7_WEB_PORT}:127.0.0.1:${AGENT7_WEB_PORT} USER@VPS"
log "============================================================"
log ""

sudo -u "$RUN_AS" env AGENT7_FACEBOOK_PROFILE_DIR="$AGENT7_FACEBOOK_PROFILE_DIR" \
  PYTHONPATH="$PYTHONPATH" DISPLAY="$DISPLAY" \
  "$PY" "$APP_ROOT/agent_6_qualifier/scripts/facebook_agent7_login.py" &
LOGIN_PID=$!

wait "$LOGIN_PID"
EXIT=$?
if [[ "$EXIT" -eq 0 ]]; then
  chown -R "$RUN_AS:$RUN_AS" "$AGENT7_FACEBOOK_PROFILE_DIR" 2>/dev/null || true
  log "Login OK — cookies saved in $AGENT7_FACEBOOK_PROFILE_DIR"
else
  log "Login не завершён. Профиль не авторизован."
fi
exit "$EXIT"
