#!/usr/bin/env bash
# Пошаговая цепочка публикации. Один live-запуск = один канал, затем выход.
# Межпроцессный lock берёт сам publisher_social. Повторная публикация возможна
# только при явно переданном --reset-channel.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PAGE_ID="${1:?page-id required}"
shift

cd "$ROOT"
export PYTHONPATH=src
exec python3 -m publisher_social publish-chain \
  --page-id "$PAGE_ID" \
  "$@"
