#!/usr/bin/env bash
# Install / refresh Open Home API edge (systemd + nginx) on Ubuntu 24.04 VPS.
#
# Safe defaults:
# - does NOT write secrets
# - does NOT enable Agent7 / amo chat live flags
# - does NOT SSH/DNS from this Mac — run ON THE VPS as root
# - does NOT enable UFW automatically (SSH lockout risk)
#
# Usage (on VPS):
#   sudo bash deploy/install_api_server.sh
# Optional overrides:
#   OPENHOME_ROOT=/opt/openhome RUN_USER=openhome sudo -E bash deploy/install_api_server.sh
#
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root on the VPS: sudo bash deploy/install_api_server.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OPENHOME_ROOT="${OPENHOME_ROOT:-/opt/openhome}"
RUN_USER="${RUN_USER:-openhome}"
APP_DIR="${OPENHOME_ROOT}/app"
VENV_DIR="${OPENHOME_ROOT}/venv"
RUNTIME_DIR="${OPENHOME_ROOT}/runtime"
LOGS_DIR="${OPENHOME_ROOT}/logs"
ENV_FILE="${OPENHOME_ROOT}/.env"
AGENT6_DIR="${APP_DIR}/agent_6_qualifier"
SERVICE_NAME="openhome-api"
NGINX_SITE="api.open-home.online"

echo "==> Open Home API install"
echo "    REPO_ROOT=$REPO_ROOT"
echo "    OPENHOME_ROOT=$OPENHOME_ROOT"
echo "    RUN_USER=$RUN_USER"

if ! id -u "$RUN_USER" >/dev/null 2>&1; then
  echo "==> Creating user $RUN_USER"
  useradd --system --create-home --home-dir "$OPENHOME_ROOT" --shell /usr/sbin/nologin "$RUN_USER"
fi

BACKUPS_DIR="${OPENHOME_ROOT}/backups"

echo "==> Creating directories"
mkdir -p "$APP_DIR" "$VENV_DIR" "$LOGS_DIR" "$BACKUPS_DIR" \
  "$RUNTIME_DIR/browser_profiles" \
  "$RUNTIME_DIR/agent7_browser_failures" \
  "$RUNTIME_DIR/publisher" \
  "$RUNTIME_DIR/sessions" \
  "$RUNTIME_DIR/contracts" \
  "$RUNTIME_DIR/state" \
  "$RUNTIME_DIR/stores" \
  "$RUNTIME_DIR/logs"

# rsync dest is APP_DIR only. Never --delete into OPENHOME_ROOT (would wipe
# /opt/openhome/runtime, /opt/openhome/.env, /opt/openhome/backups).
if [[ "$APP_DIR" == "$OPENHOME_ROOT" ]]; then
  echo "REFUSE: APP_DIR must not equal OPENHOME_ROOT (rsync --delete would wipe runtime/env/backups)"
  exit 1
fi
if [[ "$REPO_ROOT" == "$OPENHOME_ROOT" ]]; then
  echo "REFUSE: run installer from the app checkout, not OPENHOME_ROOT"
  exit 1
fi

# Sync code if installer is not already running from /opt/openhome/app
if [[ "$REPO_ROOT" != "$APP_DIR" ]]; then
  echo "==> Syncing project tree into $APP_DIR (rsync; excludes .git/.venv/profiles + mutable state)"
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude '.venv311/' \
    --exclude '**/.venv/' \
    --exclude '**/.venv311/' \
    --exclude 'venv/' \
    --exclude '**/node_modules/' \
    --exclude '**/__pycache__/' \
    --exclude '**/.pytest_cache/' \
    --exclude 'runtime/' \
    --exclude 'backups/' \
    --exclude '**/.fb_profile/' \
    --exclude '**/.env' \
    --exclude '*.session' \
    --exclude '*.session-journal' \
    --exclude '**/publications.sqlite3' \
    --exclude '**/postmypost_publications.json' \
    --exclude '**/data/contracts/' \
    --exclude '**/data/sessions/' \
    --exclude '**/amo_task_state.json' \
    --exclude '**/owners.json' \
    --exclude '**/owner_requests.json' \
    --exclude '**/amo_chat_mirrors.json' \
    --exclude '**/error_notify_state.json' \
    --exclude '**/Users.txt' \
    --exclude '**/postmypost_ai_agent_state.json' \
    --exclude '**/agent10.sqlite3' \
    "$REPO_ROOT/" "$APP_DIR/"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "==> Creating $ENV_FILE from template (FILL SECRETS MANUALLY)"
  cp "$SCRIPT_DIR/.env.production.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  chown "$RUN_USER:$RUN_USER" "$ENV_FILE"
else
  echo "==> Keeping existing $ENV_FILE (not overwritten)"
  chmod 600 "$ENV_FILE"
  chown "$RUN_USER:$RUN_USER" "$ENV_FILE"
fi

echo "==> Python venv + Agent6 requirements"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip rsync nginx
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$AGENT6_DIR/requirements.txt"

echo "==> Playwright Chromium (+ OS deps) for Agent7 FB/Airbnb transports"
"$VENV_DIR/bin/python" -m playwright install --with-deps chromium || {
  echo "WARN: playwright install failed — API health still works; browser channels later"
}

echo "==> FB parser .venv311 (Python 3.11 + requests/crawl4ai; never rsync this tree)"
export FB_ROOT="${APP_DIR}/agent_1_parser/fb_parser"
export RUN_USER
if ! bash "${FB_ROOT}/scripts/ensure_venv.sh"; then
  echo "ERROR: FB parser venv not ready. Agent 1 will refuse FB Marketplace links until:"
  echo "  sudo FB_ROOT=$FB_ROOT RUN_USER=$RUN_USER bash $FB_ROOT/scripts/ensure_venv.sh"
fi

chown -R "$RUN_USER:$RUN_USER" "$OPENHOME_ROOT"

VENV_PYTHON="$VENV_DIR/bin/python"
UNIT_SRC="$SCRIPT_DIR/systemd/openhome-api.service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"

echo "==> Installing systemd unit $SERVICE_NAME"
sed \
  -e "s|REPLACE_USER|$RUN_USER|g" \
  -e "s|REPLACE_AGENT6_DIR|$AGENT6_DIR|g" \
  -e "s|REPLACE_VENV_PYTHON|$VENV_PYTHON|g" \
  -e "s|REPLACE_ENV_FILE|$ENV_FILE|g" \
  -e "s|REPLACE_RUNTIME_DIR|$RUNTIME_DIR|g" \
  "$UNIT_SRC" > "$UNIT_DST"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "==> Installing NGINX site (HTTP; Certbot adds TLS later)"
NGINX_AVAIL="/etc/nginx/sites-available/${NGINX_SITE}.conf"
NGINX_ENABLED="/etc/nginx/sites-enabled/${NGINX_SITE}.conf"
cp "$SCRIPT_DIR/nginx/api.open-home.online.conf" "$NGINX_AVAIL"
ln -sfn "$NGINX_AVAIL" "$NGINX_ENABLED"
# Remove default site if present (optional)
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  rm -f /etc/nginx/sites-enabled/default
fi

echo "==> nginx -t (mandatory before reload)"
nginx -t
systemctl reload nginx

echo "==> Local health probe"
sleep 1
if curl -fsS "http://127.0.0.1:8000/health" >/dev/null; then
  echo "HEALTH: PASS (127.0.0.1:8000/health)"
else
  echo "HEALTH: FAIL — check: journalctl -u $SERVICE_NAME -n 80 --no-pager"
  exit 2
fi

cat <<EOF

============================================================
INSTALL COMPLETE (live flags remain OFF)

Next on this VPS:
  1) Edit secrets:  sudo -u $RUN_USER nano $ENV_FILE
  2) SSL:           sudo apt install -y certbot python3-certbot-nginx
                    sudo certbot --nginx -d api.open-home.online
                    sudo certbot renew --dry-run
  3) Public check:  curl -i https://api.open-home.online/health
  4) Unknown scope: curl -i https://api.open-home.online/webhooks/amo-chat/unknown
  5) Status:        cd $APP_DIR && $VENV_PYTHON scripts/production_status.py
  6) DO NOT enable Agent7 / amo chat live yet.

Firewall (manual — enable ONLY after SSH works):
  sudo ufw allow OpenSSH
  sudo ufw allow 'Nginx Full'
  sudo ufw enable

Logs:
  journalctl -u $SERVICE_NAME -f
============================================================
EOF
