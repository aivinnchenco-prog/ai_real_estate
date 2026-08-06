#!/bin/bash
# Настройка GitHub Actions secrets для автодеплоя на Hetzner.
# Требует: gh auth login
set -euo pipefail

REPO="${1:-bvinnchenco-cmyk/Agent-real-estate}"
HOST="${DEPLOY_HOST:-188.245.82.88}"
USER="${DEPLOY_USER:-root}"
KEY_FILE="${DEPLOY_KEY_FILE:-$HOME/.ssh/hetzner-bot}"

if ! command -v gh >/dev/null 2>&1; then
  echo "Установи GitHub CLI: brew install gh"
  echo "Затем: gh auth login"
  exit 1
fi

if [[ ! -f "$KEY_FILE" ]]; then
  echo "SSH ключ не найден: $KEY_FILE"
  exit 1
fi

echo "Repo: $REPO"
echo "Host: $HOST"

gh secret set DEPLOY_HOST --repo "$REPO" --body "$HOST"
gh secret set DEPLOY_USER --repo "$REPO" --body "$USER"
gh secret set DEPLOY_SSH_KEY --repo "$REPO" < "$KEY_FILE"

echo ""
echo "Готово. Проверка:"
gh secret list --repo "$REPO"
echo ""
echo "Следующий push в main запустит deploy workflow."
