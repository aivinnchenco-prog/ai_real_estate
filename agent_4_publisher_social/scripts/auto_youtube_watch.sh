#!/usr/bin/env bash
# Автоповтор YouTube Shorts + отложенные карусели (без участия оператора).
# Запуск: ./scripts/auto_youtube_watch.sh PAGE_ID OBJECT_ID
set -euo pipefail

PAGE_ID="${1:?page-id required}"
OBJECT_ID="${2:?object-id required}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/src"
export PYTHONUNBUFFERED=1
LOG="${ROOT}/data/logs/auto_watch_${OBJECT_ID}.log"
INTERVAL="${AUTO_WATCH_INTERVAL_SEC:-180}"
MAX_ROUNDS="${AUTO_WATCH_MAX_ROUNDS:-40}"

mkdir -p "${ROOT}/data/logs"
cd "${ROOT}"

log() { echo "[$(date -Iseconds)] $*" | tee -a "${LOG}"; }

is_done() {
  python3 - <<PY
from publisher_social.state import load_state, is_channel_done
print("yes" if is_channel_done(load_state(), "${OBJECT_ID}", "youtube_shorts") else "no")
PY
}

device_ok() {
  python3 -m publisher_social check-device 2>/dev/null | grep -q '"ok": true'
}

log "watch start page=${PAGE_ID} object=${OBJECT_ID} interval=${INTERVAL}s max_rounds=${MAX_ROUNDS}"

round=0
while (( round < MAX_ROUNDS )); do
  round=$((round + 1))

  if [[ "$(is_done)" == "yes" ]]; then
    log "youtube_shorts already done — exit"
    exit 0
  fi

  if device_ok; then
    log "round ${round}: publish-scheduled (carousels)"
    python3 -m publisher_social publish-scheduled --live 2>&1 | tee -a "${LOG}" || true

    if [[ "$(is_done)" != "yes" ]]; then
      log "round ${round}: youtube retry (live, max 2 attempts)"
      python3 -m publisher_social publish-retry \
        --page-id "${PAGE_ID}" \
        --channel youtube_shorts \
        --live \
        --max-attempts 2 2>&1 | tee -a "${LOG}" || true
    fi

    if [[ "$(is_done)" == "yes" ]]; then
      log "youtube_shorts OK — exit"
      exit 0
    fi
  else
    log "round ${round}: phone not connected — wait"
  fi

  sleep "${INTERVAL}"
done

log "max rounds reached without youtube_shorts success"
exit 1
