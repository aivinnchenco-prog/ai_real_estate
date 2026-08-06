#!/usr/bin/env bash
# Держит Mac price-worker живым: при падении Chrome/Selenium перезапускает.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="${ROOT}/data/price_worker_local.log"
mkdir -p "${ROOT}/data"
export PYTHONUNBUFFERED=1
MIN_MONTHS="${PRICE_MIN_MONTHS:-3}"
INTERVAL="${PRICE_WORKER_INTERVAL:-30}"

echo "$(date '+%F %T') | keepalive start (min_months=${MIN_MONTHS})" >>"$LOG"
while true; do
  .venv/bin/python scripts/price_worker_local.py --watch --min-months "$MIN_MONTHS" --interval "$INTERVAL" >>"$LOG" 2>&1
  code=$?
  echo "$(date '+%F %T') | worker exited code=${code}, restart in 5s" >>"$LOG"
  sleep 5
done
