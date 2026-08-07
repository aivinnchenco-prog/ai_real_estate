#!/bin/bash
# Install Agent 9 connector systemd service (isolated from Agents 1-8).
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
RUN_USER="${RUN_USER:-${SUDO_USER:-root}}"
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install_agent9.sh"
  exit 1
fi
chmod +x "$INSTALL_DIR/scripts/start_connector.sh"
sed \
  -e "s|REPLACE_AGENT9_DIR|$INSTALL_DIR|g" \
  -e "s|REPLACE_USER|$RUN_USER|g" \
  "$INSTALL_DIR/deploy/agent9-connector.service" > /etc/systemd/system/agent9-connector.service
systemctl daemon-reload
systemctl enable agent9-connector.service
echo "Installed agent9-connector.service (not started automatically unless you systemctl start it)."
