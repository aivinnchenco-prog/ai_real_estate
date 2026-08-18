#!/bin/bash
# Create isolated Facebook browser profile directories (no login).
set -euo pipefail

RUN_AS="${OPENHOME_RUN_AS:-openhome}"
BASE="${OPENHOME_RUNTIME:-/opt/openhome/runtime}"

DIRS=(
  "$BASE/browser_profiles/facebook_owner_outreach"
  "$BASE/browser_profiles/facebook_agent7"
  "$BASE/browser_profiles/facebook_agent9"
  "$BASE/state/shared/locks"
)

for dir in "$DIRS"; do
  mkdir -p "$dir"
  chown "$RUN_AS:$RUN_AS" "$dir" 2>/dev/null || true
  echo "ready: $dir"
done

echo "Legacy Agent9 profile (backup, do not delete until re-auth):"
echo "  $BASE/state/agent9/facebook_profile"
