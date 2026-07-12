#!/usr/bin/env bash
# Quick health check for 24/7 stack. Exit 0 = all OK, 1 = something broken.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

FAIL=0
ok() { echo "  OK  $*"; }
bad() { echo "  FAIL $*"; FAIL=1; }

echo "==> healthcheck $(date -u +%Y-%m-%dT%H:%M:%SZ)"

command -v python3 >/dev/null && ok python3 || bad python3
command -v node >/dev/null && ok node || bad node
command -v ffmpeg >/dev/null && ok ffmpeg || bad ffmpeg

if curl -fsS "http://127.0.0.1:8077/health" >/dev/null 2>&1; then
  ok "curator :8077"
else
  bad "curator :8077 (run: docker compose up -d curator)"
fi

if [[ -f "$ROOT/.env.real-estate" ]]; then
  ok ".env.real-estate"
else
  bad ".env.real-estate missing"
fi

if docker compose ps chain 2>/dev/null | grep -qiE 'up|running'; then
  ok "docker chain"
elif "$ROOT/scripts/chain_watcher.sh" status >/dev/null 2>&1; then
  ok "chain_watcher (native)"
else
  bad "chain (run: ./start.sh)"
fi

if command -v higgsfield >/dev/null 2>&1; then
  if higgsfield auth token >/dev/null 2>&1; then
    ok "higgsfield CLI auth"
  else
    bad "higgsfield CLI not authed (Seedance track B will-change)"
  fi
else
  bad "higgsfield CLI not installed"
fi

if [[ -f "$ROOT/data/logs/chain_watcher.log" ]]; then
  tail -1 "$ROOT/data/logs/chain_watcher.log" 2>/dev/null | sed 's/^/  log: /' || true
fi

exit "$FAIL"
