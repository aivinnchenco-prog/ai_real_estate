#!/usr/bin/env bash
# Единая точка старта 24/7: curator + chain watcher.
# Из корня workspace: ./start.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
chmod +x scripts/*.sh 2>/dev/null || true

echo "==> Real Estate services (24/7)"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found — native chain_watcher"
  ./scripts/chain_watcher.sh start
  exit 0
fi

docker compose up -d --build
echo ""
docker compose ps
echo ""
./scripts/healthcheck_vps.sh || true
echo ""
echo "Logs: docker compose logs -f chain"
