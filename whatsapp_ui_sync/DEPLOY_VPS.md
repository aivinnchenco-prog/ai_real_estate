# WhatsApp UI Lists sync — VPS deploy (Ubuntu)

## Why not Playwright-launched Chrome?

Playwright injects automation flags → WhatsApp Web serves a **different UI**
without working native Lists. Production must use **normal Chrome + CDP**.

## Architecture on Ubuntu VPS

```
Agent 6/7 (canonical role)
   → enqueue role_sync_jobs (fail-safe, never blocks qualification)
        → whatsapp-ui-sync.timer (every 2 min)
             → attach CDP → native Chrome Lists UI
```

systemd:
- `whatsapp-ui-chrome.service` — Chrome under Xvfb, CDP `127.0.0.1:9222`
- `whatsapp-ui-sync.timer` — process outbox

## Ubuntu packages

```bash
sudo apt update
sudo apt install -y xvfb

# Option A — Google Chrome (recommended; same as local Lists UI)
wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y /tmp/chrome.deb

# Option B — Chromium from Ubuntu repos
# sudo apt install -y chromium-browser
# then: WHATSAPP_UI_CHROME_BIN=$(command -v chromium-browser || command -v chromium)
```

## Install units

```bash
cd /path/to/Real\ Estate\ Agent\ -\ refactor
sudo bash whatsapp_ui_sync/deploy/install_whatsapp_ui_sync.sh
```

## One-time QR login (after full deploy + checks)

```bash
systemctl status whatsapp-ui-chrome.service

WHATSAPP_UI_CDP_URL=http://127.0.0.1:9222 \
  python3 scripts/whatsapp_ui_login.py

# No GUI on Ubuntu server: copy QR screenshot and scan on phone
# scp user@vps:~/.openhome/whatsapp_ui_sync/qr.png .
# Do NOT remove Wazzup linked device.
```

## Env (agent_6_qualifier/.env or root .env)

```
WHATSAPP_UI_CDP_URL=http://127.0.0.1:9222
WHATSAPP_UI_USE_XVFB=true
WHATSAPP_UI_PROFILE_DIR=/home/USER/.openhome/whatsapp_ui_chrome_native
WHATSAPP_UI_SYNC_ENABLED=false   # true only after QR login OK
WHATSAPP_UI_DRY_RUN=true         # false only for real list writes
WHATSAPP_UI_CLIENT_LIST_NAME=Client
WHATSAPP_UI_OWNER_LIST_NAME=Owner
WHATSAPP_UI_AGENT_LIST_NAME=Owner
```

## Smoke on Ubuntu

```bash
python3 scripts/whatsapp_ui_diagnose.py
python3 scripts/whatsapp_ui_sync_contact.py --phone +66625124001 --role CLIENT --dry-run
```

## Safety

- Only list membership (Owner/Client); no WhatsApp messages
- Never auto-create lists
- Qualification / Wazzup / amoCRM unchanged and non-blocking
- If Chrome logged out → re-run `whatsapp_ui_login.py` manually
