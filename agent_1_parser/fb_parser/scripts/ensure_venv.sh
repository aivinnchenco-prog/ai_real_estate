#!/usr/bin/env bash
# Create / repair Agent 1B FB parser venv (Python 3.11 + crawl4ai + requests).
#
# Why this exists:
#   Production Agent 1 runs from /opt/openhome/venv (Ubuntu 3.12). FB parser
#   must NOT use that interpreter — silent fallback caused:
#     ModuleNotFoundError: No module named 'requests'
#   crawl4ai/lxml also need 3.11.
#
# Usage (on VPS as root, or with write access to FB_ROOT):
#   sudo bash agent_1_parser/fb_parser/scripts/ensure_venv.sh
#   FB_ROOT=/opt/openhome/app/agent_1_parser/fb_parser RUN_USER=openhome sudo -E bash ...
#
# Probe only (no install):
#   bash agent_1_parser/fb_parser/scripts/ensure_venv.sh --probe
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FB_ROOT="${FB_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RUN_USER="${RUN_USER:-openhome}"
VENV_DIR="${FB_ROOT}/.venv311"
PYTHON_BIN="${VENV_DIR}/bin/python"
REQ_FILE="${FB_ROOT}/requirements.txt"
# agent_1_parser/fb_parser -> monorepo root (openhome_shared lives here)
APP_ROOT="${OPENHOME_APP_ROOT:-$(cd "$FB_ROOT/../.." && pwd)}"
if [[ -d "$APP_ROOT/openhome_shared" ]]; then
  export PYTHONPATH="${APP_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
  export OPENHOME_APP_ROOT="${OPENHOME_APP_ROOT:-$APP_ROOT}"
fi
PROBE_ONLY=0
if [[ "${1:-}" == "--probe" ]]; then
  PROBE_ONLY=1
fi

write_app_pth() {
  local py="${1:-$PYTHON_BIN}"
  [[ -x "$py" && -d "$APP_ROOT/openhome_shared" ]] || return 0
  local site
  site="$("$py" -c "import site; print(site.getsitepackages()[0])")"
  mkdir -p "$site"
  printf '%s\n' "$APP_ROOT" > "$site/openhome_app.pth"
  echo "==> wrote $site/openhome_app.pth -> $APP_ROOT"
}

probe() {
  local py="${1:-$PYTHON_BIN}"
  [[ -x "$py" ]] || return 1
  "$py" -c "
import sys
assert sys.version_info[:2] == (3, 11), sys.version
import requests
import playwright
import crawl4ai
import openhome_shared
print('ok', sys.version.split()[0])
" >/dev/null 2>&1
}

if [[ ! -f "$FB_ROOT/agent1b/fb_parser.py" ]]; then
  echo "ERROR: fb_parser.py not found under $FB_ROOT" >&2
  exit 1
fi
if [[ ! -f "$REQ_FILE" ]]; then
  echo "ERROR: missing $REQ_FILE" >&2
  exit 1
fi

if [[ -x "$PYTHON_BIN" ]]; then
  write_app_pth "$PYTHON_BIN" || true
fi

if probe; then
  echo "FB parser venv OK: $PYTHON_BIN"
  exit 0
fi

if [[ "$PROBE_ONLY" -eq 1 ]]; then
  echo "FB parser venv NOT READY: $PYTHON_BIN" >&2
  exit 2
fi

echo "==> FB parser venv needs install/repair ($VENV_DIR)"

if [[ "$(id -u)" -eq 0 ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3-venv python3-pip ca-certificates \
    software-properties-common gcc libxml2-dev libxslt1-dev || true
  if ! command -v python3.11 >/dev/null 2>&1; then
    echo "==> Installing Python 3.11 (deadsnakes PPA — Ubuntu 24.04 ships 3.12)"
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -y
    apt-get install -y python3.11 python3.11-venv python3.11-dev
  fi
fi

if ! command -v python3.11 >/dev/null 2>&1; then
  echo "ERROR: python3.11 not on PATH. On Ubuntu 24.04: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.11 python3.11-venv python3.11-dev" >&2
  exit 1
fi

# Broken venv (rsync of a Mac .venv311, wrong version, unexecutable binary).
if [[ -e "$PYTHON_BIN" ]] && ! "$PYTHON_BIN" -c "import sys; raise SystemExit(0 if sys.version_info[:2]==(3,11) else 1)" >/dev/null 2>&1; then
  echo "==> Rebuilding broken .venv311 (wrong OS/version or damaged interpreter)"
  rm -rf "$VENV_DIR"
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "==> Creating $VENV_DIR"
  python3.11 -m venv "$VENV_DIR"
fi

"$PYTHON_BIN" -m pip install --upgrade pip wheel
"$PYTHON_BIN" -m pip install -r "$REQ_FILE"
"$PYTHON_BIN" -m playwright install chromium || {
  echo "WARN: playwright install chromium failed — login_fb.py / crawl4ai need it" >&2
}
write_app_pth "$PYTHON_BIN" || true

if [[ "$(id -u)" -eq 0 ]] && id -u "$RUN_USER" >/dev/null 2>&1; then
  chown -R "$RUN_USER:$RUN_USER" "$VENV_DIR"
fi

if ! probe; then
  echo "ERROR: FB parser venv still missing requests/playwright/crawl4ai after install" >&2
  echo "       python=$PYTHON_BIN" >&2
  exit 1
fi

echo "FB parser venv READY: $PYTHON_BIN"
echo "Set in .env / systemd:"
echo "  FB_PARSER_ROOT=$FB_ROOT"
echo "  FB_PARSER_PYTHON=$PYTHON_BIN"
