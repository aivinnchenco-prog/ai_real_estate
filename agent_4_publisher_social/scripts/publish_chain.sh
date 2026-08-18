#!/usr/bin/env bash
# Пошаговая цепочка публикации. Один live-запуск = один канал, затем выход.
# Межпроцессный lock берёт сам publisher_social. Повторная публикация возможна
# только при явно переданном --reset-channel.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PAGE_ID="${1:?page-id required}"
shift

cd "$ROOT"
export PYTHONPATH="src:.."
export PUBLISHER_FB_BACKEND="${PUBLISHER_FB_BACKEND:-browser}"
export AGENT7_FACEBOOK_PROFILE_DIR="${AGENT7_FACEBOOK_PROFILE_DIR:-$HOME/openhome/facebook_agent7}"
export OPENHOME_FB_LOCK_DIR="${OPENHOME_FB_LOCK_DIR:-$ROOT/data/locks}"
export AGENT7_FB_HEADLESS="${AGENT7_FB_HEADLESS:-false}"
exec python3 -m publisher_social publish-chain \
  --page-id "$PAGE_ID" \
  "$@"
