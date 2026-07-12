#!/usr/bin/env bash
# Поднять фоновые сервисы проекта (curator + chain watcher), если ещё не запущены.
# Вызывается из run_pipeline.sh и может дергаться агентом при старте.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  # Fallback без Docker: только native chain_watcher
  if [[ -x "$ROOT/scripts/chain_watcher.sh" ]]; then
    "$ROOT/scripts/chain_watcher.sh" start 2>/dev/null || true
  fi
  exit 0
fi

if [[ -f docker-compose.yml ]] || [[ -f compose.yaml ]]; then
  docker compose up -d --remove-orphans 2>/dev/null || docker-compose up -d --remove-orphans 2>/dev/null || true
fi
