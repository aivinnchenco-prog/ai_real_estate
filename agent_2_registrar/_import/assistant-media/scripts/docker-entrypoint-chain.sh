#!/usr/bin/env bash
set -euo pipefail
cd /app
if [[ -f package.json ]] && [[ ! -d node_modules/@aws-sdk ]]; then
  npm install --omit=dev 2>/dev/null || true
fi
exec "$@"
