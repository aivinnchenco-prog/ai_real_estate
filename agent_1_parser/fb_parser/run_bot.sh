#!/usr/bin/env bash
# Start Agent 1B Telegram bot from any directory.
set -euo pipefail

cd "$(dirname "$0")"

VENV=""
for candidate in .venv311 .venv; do
  if [ -f "$candidate/bin/activate" ]; then
    VENV="$candidate"
    break
  fi
done

if [ -z "$VENV" ]; then
  echo "ERROR: no virtualenv found (.venv311 or .venv). See README: Install." >&2
  exit 1
fi

source "$VENV/bin/activate"
exec python agent1b/tg_bot.py
