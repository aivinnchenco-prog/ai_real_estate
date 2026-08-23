# Open Home Production API (`api.open-home.online`)

Production backend for the **multi-agent system** (Agents 1–10, Agent7, amoCRM Chat webhooks).

This is **not** the public marketing website (separate VPS).

| Item | Value |
|---|---|
| Domain | `https://api.open-home.online` |
| DNS A | `72.60.108.152` |
| OS | Ubuntu 24.04 LTS |
| App bind | `127.0.0.1:8000` (loopback only) |
| Public ports | `22`, `80`, `443` |
| Entrypoint | `agent_6_qualifier/scripts/amo_chat_webhook_serve.py` |
| Service | `openhome-api.service` |

## Canonical layout

| Role | Path |
|------|------|
| APP | `/opt/openhome/app` |
| ENV | `/opt/openhome/.env` |
| RUNTIME | `/opt/openhome/runtime` |
| VENV | `/opt/openhome/venv` |
| BACKUPS | `/opt/openhome/backups` |

Code deploys into APP. Mutable production state lives in RUNTIME. Secrets live in ENV. Do not rsync `--delete` into `/opt/openhome`.

**Live outreach stays OFF after deploy.** Enabling Agent7 / amo chat live is a later step.

---

## 0. What this package reuses

- Existing stdlib webhook server (`amo_chat_webhook_serve.py`) — **no second parallel backend**
- Existing amo chat routing / loop guards / dry-run edge
- Existing Agent6 `requirements.txt` + Playwright for FB/Airbnb transports
- Wazzup remains on its own process/port (`8765`); this API owns `:8000`

---

## 1. SSH to the VPS

```bash
ssh <your-user>@72.60.108.152
```

Copy the project onto the server (example). Dest must be **`/opt/openhome/app`**, never `/opt/openhome` (rsync `--delete` would wipe runtime, `.env`, backups):

```bash
sudo mkdir -p /opt/openhome
sudo chown "$USER":"$USER" /opt/openhome
# from your laptop (example — run locally, not inside Cursor auto-deploy):
# rsync -a --delete --exclude '.git' --exclude '.venv' --exclude '.venv311' --exclude '**/.venv311' --exclude '**/.env' \
#   --exclude '*.session' --exclude '**/publications.sqlite3' \
#   "/path/to/Real Estate Agent - refactor/" user@72.60.108.152:/opt/openhome/app/
```

Or clone/rsync however you normally ship this monorepo. Do **not** copy filled `.env` over SSH in chat logs.

---

## 2. Create production user and directories

```bash
sudo useradd --system --create-home --home-dir /opt/openhome --shell /usr/sbin/nologin openhome || true
sudo mkdir -p /opt/openhome/{app,venv,backups,runtime/{browser_profiles,agent7_browser_failures,publisher,sessions,contracts,state,stores,logs},logs}
sudo chown -R openhome:openhome /opt/openhome
```

Facebook/Airbnb Playwright profiles must be owned by the **same** user that runs `openhome-api` (`openhome`).

---

## 3. Copy / sync project into `/opt/openhome/app`

Ensure:

```text
/opt/openhome/app/agent_6_qualifier/scripts/amo_chat_webhook_serve.py
/opt/openhome/app/deploy/...
```

---

## 4. Create venv and install dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx rsync
sudo -u openhome python3 -m venv /opt/openhome/venv
sudo -u openhome /opt/openhome/venv/bin/pip install --upgrade pip
sudo -u openhome /opt/openhome/venv/bin/pip install -r /opt/openhome/app/agent_6_qualifier/requirements.txt
```

FB Marketplace parser is a **separate Python 3.11 venv**. Do not install its deps into `/opt/openhome/venv` (Ubuntu 24.04 is 3.12; silent reuse of that interpreter caused `ModuleNotFoundError: requests`). Never rsync `--delete` `.venv311` — a Mac venv copied to Linux, or a later deploy wiping the server venv, will break Agent 1 FB links.

```bash
sudo FB_ROOT=/opt/openhome/app/agent_1_parser/fb_parser RUN_USER=openhome \
  bash /opt/openhome/app/agent_1_parser/fb_parser/scripts/ensure_venv.sh
```

`install_api_server.sh` runs this after rsync (and excludes `.venv311` so `--delete` cannot wipe it).

---

## 5. Install Playwright browser deps

```bash
sudo /opt/openhome/venv/bin/python -m playwright install --with-deps chromium
sudo chown -R openhome:openhome /opt/openhome/runtime
```

Do **not** run Facebook/Airbnb login or live sends yet.

---

## 6. Create production `.env`

```bash
sudo cp /opt/openhome/app/deploy/.env.production.example /opt/openhome/.env
sudo chown openhome:openhome /opt/openhome/.env
sudo chmod 600 /opt/openhome/.env
sudo -u openhome nano /opt/openhome/.env
```

Fill secrets from your existing local credentials. Keep:

```env
AMO_CHAT_PUBLIC_BASE_URL=https://api.open-home.online
AMO_CHAT_WEBHOOK_ENABLED=false
AMO_CHAT_MIRROR_LIVE=false
AMO_CHAT_CONNECT_LIVE=false
AGENT7_LIVE_OUTREACH_ENABLED=false
AGENT7_FACEBOOK_MESSENGER_ENABLED=false
AGENT7_AIRBNB_MESSAGES_ENABLED=false
```

Telegram showcase (Agent 4, posts to the channel right after parsing):

```env
TG_BOT_TOKEN_PUBLISHER=          # trip_home_phuket_bot — must be channel admin
TELEGRAM_CHANNEL=@OpenHome_th
```

Do **not** put a parser bot token into `TELEGRAM_BOT_TOKEN` in this shared file.
The publisher reads `TG_BOT_TOKEN_PUBLISHER` first.

Do **not** invent a new amoCRM long-lived token.

---

## 7. Install systemd + NGINX (helper)

From the app tree on the VPS:

```bash
cd /opt/openhome/app
sudo bash deploy/install_api_server.sh
```

Or install units manually:

```bash
sudo sed \
  -e 's|REPLACE_USER|openhome|g' \
  -e 's|REPLACE_AGENT6_DIR|/opt/openhome/app/agent_6_qualifier|g' \
  -e 's|REPLACE_VENV_PYTHON|/opt/openhome/venv/bin/python|g' \
  -e 's|REPLACE_ENV_FILE|/opt/openhome/.env|g' \
  -e 's|REPLACE_RUNTIME_DIR|/opt/openhome/runtime|g' \
  deploy/systemd/openhome-api.service \
  | sudo tee /etc/systemd/system/openhome-api.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable openhome-api
sudo systemctl restart openhome-api
sudo systemctl status openhome-api --no-pager
```

Logs:

```bash
journalctl -u openhome-api -f
```

---

## 8. NGINX site (HTTP first)

```bash
sudo cp /opt/openhome/app/deploy/nginx/api.open-home.online.conf \
  /etc/nginx/sites-available/api.open-home.online.conf
sudo ln -sfn /etc/nginx/sites-available/api.open-home.online.conf \
  /etc/nginx/sites-enabled/api.open-home.online.conf
sudo nginx -t
sudo systemctl reload nginx
```

Local upstream check:

```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1/health
```

---

## 9. HTTPS with Let's Encrypt (Certbot)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d api.open-home.online
sudo certbot renew --dry-run
```

Certbot will add `listen 443 ssl` and HTTP→HTTPS redirect. Do not hand-edit certificate paths unless Certbot fails.

---

## 10. Public verification

```bash
curl -i https://api.open-home.online/health
# expect HTTP 200 + {"status":"ok",...}

curl -i https://api.open-home.online/webhooks/amo-chat/unknown
# expect safe HTTP 404 (unknown scope) — do NOT send a real signed amo payload
```

Status / registration helpers (no secrets printed):

```bash
cd /opt/openhome/app
sudo -u openhome /opt/openhome/venv/bin/python scripts/production_status.py
sudo -u openhome /opt/openhome/venv/bin/python scripts/amo_chat_registration_info.py
```

Expected registration webhook:

```text
https://api.open-home.online/webhooks/amo-chat/:scope_id
```

---

## 11. Firewall (Hostinger / UFW)

**Warning:** do not `ufw enable` until SSH (22) is allowed and you have a working session.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw status
# only when sure:
sudo ufw enable
```

Public: `22/tcp`, `80/tcp`, `443/tcp`.  
**Do not** publish `8000/tcp`.

---

## 12. Backups (high level)

| Path | Backup? |
|---|---|
| `/opt/openhome/app` code | yes |
| `/opt/openhome/.env` | encrypted / restricted only |
| `/opt/openhome/runtime/` | yes (sqlite, sessions, contracts, stores, browser profiles) |
| `/opt/openhome/runtime/browser_profiles` | restricted; contains session cookies — **not** public backups |
| `/opt/openhome/backups` | keep timestamped copies; do not prune blindly |

---

## 13. What NOT to do yet

- Do **not** run `amo_chat_setup.py connect-facebook` / `connect-airbnb` until amoCRM registers channels
- Do **not** set live flags to `true`
- Do **not** send owner messages from production
- Do **not** open port 8000 publicly

---

## 14. Quick reference

```bash
sudo systemctl status openhome-api
sudo nginx -t && sudo systemctl reload nginx
journalctl -u openhome-api -n 100 --no-pager
curl -fsS https://api.open-home.online/health
```
