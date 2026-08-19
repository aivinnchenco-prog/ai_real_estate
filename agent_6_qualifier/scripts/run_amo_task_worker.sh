#!/bin/bash
# One-shot amoCRM SLA worker entrypoint (cron/systemd timer).
# Do not bash-source dotenv: production .env values can contain spaces
# and break `set -euo pipefail`. systemd EnvironmentFile + Python loader.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-src}"
PY="${OPENHOME_PYTHON:-}"
if [[ -z "$PY" && -x /opt/openhome/venv/bin/python ]]; then
  PY=/opt/openhome/venv/bin/python
fi
if [[ -z "$PY" ]]; then
  PY="$(command -v python3)"
fi
exec "$PY" scripts/amo_task_worker.py "$@"
