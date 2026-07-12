#!/bin/bash
# Обновление кода на сервере (git pull + restart). Вызывается вручную или из GitHub Actions.
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/agent-real-estate}"
SERVICE_NAME="${SERVICE_NAME:-airbnb-bot}"
BRANCH="${BRANCH:-main}"

cd "$INSTALL_DIR"

git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

.venv/bin/pip install -r requirements.txt -q

systemctl restart "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME"
echo "OK: $SERVICE_NAME обновлён и перезапущен ($(git rev-parse --short HEAD))"
