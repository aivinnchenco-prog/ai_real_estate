# Production Recovery

## What must not be lost

| Asset | Path (production) | Sensitivity |
|---|---|---|
| Production env | `/opt/openhome/.env` | **secrets** — encrypted/restricted backup only |
| App code | `/opt/openhome/app` | normal |
| Runtime (canonical) | `/opt/openhome/runtime/` | sqlite, Telethon sessions, contracts, JSON state |
| Publisher ledger | `/opt/openhome/runtime/publisher/publications.sqlite3` | publication history |
| Telethon sessions | `/opt/openhome/runtime/sessions/` | **auth — highly sensitive** |
| Contracts | `/opt/openhome/runtime/contracts/` | generated DOCX |
| Amo chat mirror store | `AMO_CHAT_MIRROR_STORE_PATH` (runtime/stores) | business mapping |
| OwnerRequest store | under Agent7 runtime/data (see `OwnerRequestStore`) | business state |
| Browser profiles | `/opt/openhome/runtime/browser_profiles/` | **cookies/session — highly sensitive** |
| Browser failure shots | `/opt/openhome/runtime/agent7_browser_failures/` | may contain PII UI |
| systemd unit | `/etc/systemd/system/openhome-api.service` | rebuildable from `deploy/systemd/` |
| nginx site | `/etc/nginx/sites-available/api.open-home.online.conf` | rebuildable; Certbot may amend SSL |

## Do not put in public backups

- `.env` plaintext in shared drives/chat
- browser profile directories (Facebook/Airbnb/WhatsApp sessions)
- channel secrets / amo tokens

## Restore API service (minimal)

```bash
# 1) restore code + venv + .env (securely)
# 2) restore mirror/owner state JSON if available
sudo systemctl daemon-reload
sudo systemctl restart openhome-api
curl -fsS http://127.0.0.1:8000/health
sudo nginx -t && sudo systemctl reload nginx
```

## Restore after accidental live enable

See `deploy/ROLLBACK.md` — disable live flags first; **do not delete** OwnerRequest / mirror mappings.

## Restart recovery (amo chat)

| Scenario | Behaviour |
|---|---|
| A. External send OK, mirror not done | Mirror is secondary + `AMO_CHAT_MIRROR_DEGRADED`; business send already marked in OwnerRequestStore. Replay uses stable msgid / duplicate guards. |
| B. Manager webhook received, send uncertain | Live path **claims msgid before send** (`imported_msgids`). Replay will not double-send. Failed send reports delivery_status; manual reconcile if needed. |
| C. Owner inbound persisted, parse unfinished | OwnerRequest / session stores keep durable state; reprocess from store, do not invent CRM chat. |
| D. Mirror OK, webhook/callback duplicated | `duplicate_msgid` / `AMO_CHAT_WEBHOOK_DUPLICATE` / loop guard → ignore. |

Persistent files survive process restart: `amo_chat_mirrors.json`, OwnerRequest store, channel `scope_id` state.

## Logs

```bash
journalctl -u openhome-api --since today
journalctl -u openhome-api -f
```

Nginx: `/var/log/nginx/api.open-home.online.*.log` (logrotate: `deploy/logrotate/api.open-home.online`).

Journald: prefer journal for app logs; avoid writing secrets. If disk pressure grows:

```bash
journalctl --disk-usage
# optional system-wide cap (review before applying):
# /etc/systemd/journald.conf → SystemMaxUse=500M
```
