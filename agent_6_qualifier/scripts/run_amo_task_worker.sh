#!/bin/bash
# One-shot amoCRM SLA worker entrypoint (cron/systemd timer).
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
export PYTHONPATH="${PYTHONPATH:-src}"
exec python3 scripts/amo_task_worker.py "$@"
