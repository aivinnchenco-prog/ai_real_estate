#!/bin/bash
# Create Open Home runtime layout on a fresh VPS (no secrets, no app code).
set -euo pipefail

OPENHOME_ROOT="${OPENHOME_ROOT:-/opt/openhome}"
RUN_AS="${OPENHOME_RUN_AS:-openhome}"

DIRS=(
  "$OPENHOME_ROOT/runtime/sessions"
  "$OPENHOME_ROOT/runtime/contracts"
  "$OPENHOME_ROOT/runtime/state/qualifier"
  "$OPENHOME_ROOT/runtime/state/agent9"
  "$OPENHOME_ROOT/runtime/stores"
  "$OPENHOME_ROOT/runtime/browser_profiles"
  "$OPENHOME_ROOT/runtime/state/shared/locks"
  "$OPENHOME_ROOT/runtime/publisher"
  "$OPENHOME_ROOT/runtime/logs"
  "$OPENHOME_ROOT/runtime/agent7_browser_failures"
  "$OPENHOME_ROOT/backups"
)

for dir in "$DIRS"; do
  mkdir -p "$dir"
  chown "$RUN_AS:$RUN_AS" "$dir" 2>/dev/null || true
  echo "ready: $dir"
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
bash "$SCRIPT_DIR/../openhome_shared/deploy/ensure_facebook_profile_dirs.sh"

echo "Runtime bootstrap complete. Copy deploy/.env.production.example -> $OPENHOME_ROOT/.env and fill secrets."
