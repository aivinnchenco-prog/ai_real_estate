#!/usr/bin/env bash
# One-time OAuth for headless VPS: browser on your Mac, callback via SSH tunnel.
#
# Step 1 — on YOUR Mac (keep open):
#   ssh -L 3843:127.0.0.1:3843 USER@YOUR_VPS
#
# Step 2 — on VPS (this script):
#   ./scripts/higgsfield_auth_remote.sh
#
# Step 3 — open the URL printed by higgsfield in your Mac browser.
#          After login, callback hits localhost:3843 → tunnel → VPS.
set -euo pipefail

PORT="${HIGGSFIELD_AUTH_PORT:-3843}"
export PATH="${HOME}/.local/bin:/usr/local/bin:${PATH}"

if ! command -v higgsfield >/dev/null 2>&1; then
  echo "Installing higgsfield CLI..."
  curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh -s -- --prefix="${HOME}/.local"
  export PATH="${HOME}/.local/bin:${PATH}"
fi

echo "=============================================="
echo " Higgsfield OAuth (headless VPS)"
echo "=============================================="
echo ""
echo "Before continuing, on your Mac run in another terminal:"
echo ""
echo "  ssh -L ${PORT}:127.0.0.1:${PORT} \$(whoami)@$(hostname -f 2>/dev/null || hostname)"
echo ""
echo "Then open the login URL (below) in your Mac browser."
echo "Press Enter when SSH tunnel is ready..."
read -r _

echo ""
echo "Starting higgsfield auth login --port ${PORT} ..."
higgsfield auth login --port "$PORT"

echo ""
if higgsfield auth token >/dev/null 2>&1; then
  echo "OK — CLI authenticated."
  higgsfield account credits 2>/dev/null || true
  node scripts/test_higgsfield_providers.mjs 2>/dev/null || true
else
  echo "FAILED — no token. Check tunnel and retry." >&2
  exit 1
fi
