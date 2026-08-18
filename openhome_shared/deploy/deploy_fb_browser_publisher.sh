#!/usr/bin/env bash
# Pull latest code on VPS and restart chain watcher (FB browser publisher).
set -euo pipefail

ROOT="${VPS_PROJECT_DIR:-/data/.openclaw/workspace}"
BRANCH="${DEPLOY_BRANCH:-main}"
RUN_AS="${OPENHOME_RUN_AS:-openhome}"

if [[ -d "$ROOT/.git" ]]; then
  echo "[deploy] git pull in $ROOT ($BRANCH)"
  git -C "$ROOT" fetch origin "$BRANCH"
  git -C "$ROOT" checkout "$BRANCH"
  git -C "$ROOT" pull --ff-only origin "$BRANCH"
else
  echo "[deploy] skip git: not a repo at $ROOT" >&2
fi

PROFILE_SCRIPT="$ROOT/openhome_shared/deploy/ensure_facebook_profile_dirs.sh"
if [[ -f "$PROFILE_SCRIPT" ]]; then
  sudo bash "$PROFILE_SCRIPT"
fi

COMPOSE_DIR="$ROOT/agent_2_registrar/_import/assistant-media"
if [[ -f "$COMPOSE_DIR/docker-compose.yml" ]]; then
  echo "[deploy] docker compose restart chain"
  (cd "$COMPOSE_DIR" && docker compose restart chain)
elif command -v systemctl >/dev/null 2>&1; then
  echo "[deploy] systemctl restart openhome-chain (if installed)"
  sudo systemctl restart openhome-chain 2>/dev/null || true
else
  echo "[deploy] no compose/systemd — restart chain watcher manually"
fi

echo "[deploy] done. Ensure on server:"
echo "  PUBLISHER_FB_BACKEND=browser"
echo "  AGENT7_FACEBOOK_PROFILE_DIR=/opt/openhome/runtime/browser_profiles/facebook_agent7"
echo "  OPENHOME_FB_LOCK_DIR=/opt/openhome/runtime/state/shared/locks"
echo "  AGENT7_FB_HEADLESS=true"
