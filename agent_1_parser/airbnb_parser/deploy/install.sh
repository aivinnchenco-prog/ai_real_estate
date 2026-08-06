#!/bin/bash
# Первичная установка бота на Ubuntu 22.04+ (VPS).
# Запуск на сервере: curl -fsSL ... | bash  или  bash deploy/install.sh
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/bvinnchenco-cmyk/Agent-real-estate.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/agent-real-estate}"
SERVICE_NAME="${SERVICE_NAME:-airbnb-bot}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Запусти от root: sudo bash deploy/install.sh"
  exit 1
fi

echo "==> Системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-venv python3-pip git curl wget gnupg ca-certificates \
  fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 libcups2 \
  libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3 libx11-xcb1 libxcomposite1 \
  libxdamage1 libxrandr2 xdg-utils

if ! command -v google-chrome >/dev/null 2>&1; then
  echo "==> Google Chrome (для Selenium парсера)"
  install -d -m 0755 /usr/share/keyrings
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
  echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list
  apt-get update -qq
  apt-get install -y -qq google-chrome-stable
fi

echo "==> Клонирование репозитория"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" pull origin main
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

echo "==> Python venv"
python3 -m venv .venv
.venv/bin/pip install -U pip wheel
.venv/bin/pip install -r requirements.txt

mkdir -p data credentials Logs temp_images temp_voice

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo ""
  echo "ВАЖНО: отредактируй $INSTALL_DIR/.env (TG_BOT_TOKEN, ключи API)"
  echo "Скопируй credentials/ с локального Mac (service account + drive-oauth-token.json)"
fi

echo "==> systemd"
sed "s|/opt/agent-real-estate|$INSTALL_DIR|g" deploy/airbnb-bot.service \
  > "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo ""
echo "Готово. Дальше:"
echo "  1. nano $INSTALL_DIR/.env"
echo "  2. scp credentials/* root@SERVER:$INSTALL_DIR/credentials/"
echo "  3. systemctl start $SERVICE_NAME"
echo "  4. journalctl -u $SERVICE_NAME -f"
