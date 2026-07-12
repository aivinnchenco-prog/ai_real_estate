#!/bin/bash
# Первый push проекта на GitHub. Запуск из корня репозитория.
set -euo pipefail

REPO="${1:-bvinnchenco-cmyk/Agent-real-estate}"
REMOTE_URL="${GITHUB_REMOTE_URL:-https://github.com/${REPO}.git}"

if ! command -v git >/dev/null 2>&1; then
  echo "Git не найден. Установи Xcode Command Line Tools:"
  echo "  xcode-select --install"
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  echo "Проверка: .env не должен попасть в git..."
  if git check-ignore -q .env 2>/dev/null || grep -q '^\.env$' .gitignore; then
    echo "  OK — .env в .gitignore"
  else
    echo "  ОШИБКА: добавь .env в .gitignore перед push"
    exit 1
  fi
fi

if [[ ! -d .git ]]; then
  git init -b main
fi

git add -A
git status

if git diff --cached --quiet; then
  echo "Нет изменений для коммита."
else
  git commit -m "$(cat <<'EOF'
Initial commit: Airbnb Telegram CRM bot.

Parsing, Sheets/Drive, Supabase task queue, Cursor SDK /cursor.
EOF
)"
fi

REMOTE="origin"
if git remote get-url "$REMOTE" >/dev/null 2>&1; then
  git remote set-url "$REMOTE" "$REMOTE_URL"
else
  git remote add "$REMOTE" "$REMOTE_URL"
fi

echo ""
echo "Remote: $REMOTE_URL"
echo "Пуш: git push -u origin main"
echo ""
read -r -p "Выполнить git push сейчас? [y/N] " ok
if [[ "${ok,,}" == "y" ]]; then
  git push -u origin main
  echo "Готово: https://github.com/${REPO}"
fi
