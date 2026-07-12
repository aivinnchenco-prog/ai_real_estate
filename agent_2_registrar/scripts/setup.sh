#!/usr/bin/env bash
# Deploy Real Estate Agent workspaces to OpenClaw
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "==> Real Estate Agent setup"
echo "    PROJECT_ROOT: $PROJECT_ROOT"

# Ensure data dirs
mkdir -p "$PROJECT_ROOT/data/listings" \
         "$PROJECT_ROOT/data/crm" \
         "$PROJECT_ROOT/data/media" \
         "$PROJECT_ROOT/data/publish-queue"

# Inject PROJECT_ROOT into all TOOLS.md files
for ws in coordinator parser crm video publisher; do
  TOOLS="$PROJECT_ROOT/workspaces/$ws/TOOLS.md"
  if [[ -f "$TOOLS" ]]; then
    if grep -q "PROJECT_ROOT" "$TOOLS"; then
      # Append resolved path block if not already set
      if ! grep -q "$PROJECT_ROOT" "$TOOLS" 2>/dev/null; then
        echo "" >> "$TOOLS"
        echo "## Resolved at setup" >> "$TOOLS"
        echo "PROJECT_ROOT=$PROJECT_ROOT" >> "$TOOLS"
      fi
    fi
  fi
  echo "    ✓ workspace: $ws"
done

# Generate openclaw config from template
CONFIG_OUT="${OPENCLAW_CONFIG:-$HOME/.openclaw/openclaw.json}"
TEMPLATE="$PROJECT_ROOT/deploy/openclaw.json.example"

if [[ -f "$TEMPLATE" ]]; then
  mkdir -p "$(dirname "$CONFIG_OUT")"
  sed "s|/path/to/real-estate-agent|$PROJECT_ROOT|g" \
    "$TEMPLATE" \
    > "${CONFIG_OUT}.generated"
  echo ""
  echo "==> Generated config: ${CONFIG_OUT}.generated"
  echo "    Review and copy:"
  echo "    cp ${CONFIG_OUT}.generated $CONFIG_OUT"
fi

# Env check
if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
  echo ""
  echo "==> Created .env from template — fill in API keys"
fi

echo ""
echo "==> Done. Next steps:"
echo "    1. Edit $PROJECT_ROOT/.env"
echo "    2. cp ${CONFIG_OUT}.generated ~/.openclaw/openclaw.json  (after review)"
echo "    3. openclaw gateway"
echo "    4. Send to coordinator: /pipeline <listing-url>"
