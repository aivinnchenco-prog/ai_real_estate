#!/bin/bash
# Install systemd timer for amoCRM SLA worker (one-shot, every 10 min).
# Usage (on server): sudo bash deploy/install_amo_task_worker.sh
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
RUN_USER="${RUN_USER:-${SUDO_USER:-root}}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install_amo_task_worker.sh"
  exit 1
fi

chmod +x "$INSTALL_DIR/scripts/run_amo_task_worker.sh"
chmod +x "$INSTALL_DIR/scripts/amo_task_worker.py"

for unit in amo-task-worker.service amo-task-worker.timer; do
  sed \
    -e "s|REPLACE_AGENT6_DIR|$INSTALL_DIR|g" \
    -e "s|REPLACE_USER|$RUN_USER|g" \
    "$INSTALL_DIR/deploy/$unit" > "/etc/systemd/system/$unit"
done

systemctl daemon-reload
systemctl enable --now amo-task-worker.timer
systemctl start amo-task-worker.service

echo "Installed amo-task-worker.timer (every 10 min, Persistent=true)."
echo "Check: systemctl status amo-task-worker.timer"
echo "Logs:  journalctl -u amo-task-worker.service -n 50"
