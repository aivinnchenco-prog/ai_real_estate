#!/bin/bash
# Install WhatsApp UI Chrome + list-sync systemd units on VPS.
# Usage: sudo bash whatsapp_ui_sync/deploy/install_whatsapp_ui_sync.sh
set -euo pipefail

ROOT="${INSTALL_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
RUN_USER="${RUN_USER:-${SUDO_USER:-root}}"
DEPLOY="$ROOT/whatsapp_ui_sync/deploy"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash whatsapp_ui_sync/deploy/install_whatsapp_ui_sync.sh"
  exit 1
fi

chmod +x \
  "$ROOT/scripts/run_whatsapp_ui_chrome.sh" \
  "$ROOT/scripts/run_whatsapp_ui_queue_worker.sh" \
  "$ROOT/scripts/whatsapp_ui_open_native_chrome.py" \
  "$ROOT/scripts/whatsapp_ui_queue_worker.py" \
  "$ROOT/scripts/whatsapp_ui_login.py" \
  "$ROOT/scripts/whatsapp_ui_diagnose.py" \
  "$ROOT/scripts/whatsapp_ui_sync_contact.py"

for unit in whatsapp-ui-chrome.service whatsapp-ui-sync.service whatsapp-ui-sync.timer; do
  sed \
    -e "s|REPLACE_ROOT|$ROOT|g" \
    -e "s|REPLACE_USER|$RUN_USER|g" \
    "$DEPLOY/$unit" > "/etc/systemd/system/$unit"
done

systemctl daemon-reload
systemctl enable --now whatsapp-ui-chrome.service
systemctl enable --now whatsapp-ui-sync.timer

echo "Installed:"
echo "  whatsapp-ui-chrome.service  (native Chrome + CDP :9222)"
echo "  whatsapp-ui-sync.timer      (queue every 2 min)"
echo
echo "Next (manual QR once, after full deploy checks on Ubuntu):"
echo "  sudo -u $RUN_USER WHATSAPP_UI_CDP_URL=http://127.0.0.1:9222 python3 $ROOT/scripts/whatsapp_ui_login.py"
echo "  # no GUI: scp ~/.openhome/whatsapp_ui_sync/qr.png and scan on phone"
echo
echo "Ubuntu packages if missing:"
echo "  sudo apt install -y xvfb"
echo "  # + google-chrome-stable (recommended) or chromium-browser"
echo
echo "Enable writes only after login OK:"
echo "  WHATSAPP_UI_SYNC_ENABLED=true"
echo "  WHATSAPP_UI_DRY_RUN=false"
echo "  WHATSAPP_UI_CDP_URL=http://127.0.0.1:9222"
echo
echo "Status: systemctl status whatsapp-ui-chrome.service"
echo "Logs:   journalctl -u whatsapp-ui-chrome.service -u whatsapp-ui-sync.service -n 80"
