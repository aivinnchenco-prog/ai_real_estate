# Production services map (Open Home backend VPS)

Host: `72.60.108.152` · User: `openhome`  
Live gates **OFF** · No Agent7 FB/Airbnb outreach · No amo custom chat registration · No live workers started by this persistence work

## Canonical paths

| Role | Path |
|------|------|
| APP | `/opt/openhome/app` |
| ENV | `/opt/openhome/.env` (`chmod 600`, owner `openhome`) |
| RUNTIME | `/opt/openhome/runtime` |
| VENV | `/opt/openhome/venv` |
| BACKUPS | `/opt/openhome/backups` |

Runtime layout (production state; survives app rsync):

| Path | Contents |
|------|----------|
| `/opt/openhome/runtime/publisher/publications.sqlite3` | Agent 4 publication ledger (`PUBLISHER_LEDGER_PATH`) |
| `/opt/openhome/runtime/publisher/postmypost_publications.json` | PostMyPost slot map (`PUBLISHER_PMP_STATE_PATH`) |
| `/opt/openhome/runtime/sessions/` | Telethon `.session` files (`TG_SESSION_DIR`) |
| `/opt/openhome/runtime/contracts/` | Agent 8 generated DOCX (`AGENT8_CONTRACTS_DIR`) |
| `/opt/openhome/runtime/state/` | Qualifier JSON sessions, owners, amo-task, Agent1 users, Usher |
| `/opt/openhome/runtime/stores/` | amo chat mirrors / owner requests (already env-based) |
| `/opt/openhome/runtime/browser_profiles/` | Playwright profiles |
| `/opt/openhome/runtime/logs/` | watcher logs |

Deploy rsync dest is **`/opt/openhome/app` only**. It must never `--delete` into `/opt/openhome` (would wipe runtime, `.env`, backups). See `deploy/install_api_server.sh`.

| SERVICE | PURPOSE | COMMAND | PORT | USER | AUTOSTART | HEALTHCHECK | LIVE EFFECT |
|---------|---------|---------|------|------|-----------|-------------|-------------|
| openhome-api | Health + amo Chat webhook HTTP (gates OFF → health only) | `venv/bin/python agent_6_qualifier/scripts/amo_chat_webhook_serve.py` | 127.0.0.1:8000 | openhome | **YES / running** | `curl -s http://127.0.0.1:8000/health` | None while `AMO_CHAT_WEBHOOK_ENABLED=false` |
| nginx | TLS/HTTP edge for `api.open-home.online` | nginx | 80/443 | root | **YES / running** | `systemctl is-active nginx` | Public HTTP proxy only |
| openhome-chain-watcher.service | Notion-gated Agent 2→3→4 (`chain_runner.py --watch`) | `/opt/openhome/venv/bin/python scripts/chain_runner.py --watch` | n/a | openhome | **INSTALLED, DISABLED** | journal | Would montage + PostMyPost/Telegram publish for ready objects |
| openhome-agent6.service | Agent 6 Qualifier Telethon userbot | `python -m agent6_qualifier.tg_userbot` | n/a | openhome | **INSTALLED, DISABLED** | session auth | Would read TG inbox and reply |
| openhome-agent1.service | Agent 1 parser bot (`airbnb_parser/main.py` + xvfb) | `xvfb-run … venv/bin/python -u main.py` | n/a | openhome | **INSTALLED, DISABLED** | import/config smoke only | Would poll Telegram and parse listings |
| openhome-amo-task.timer | Periodic amo SLA task reconciliation | oneshot → worker | n/a | openhome | **INSTALLED, DISABLED** | `systemctl list-timers` | Would upsert/complete amo tasks |
| openhome-amo-task.service | One-shot worker (Python env loader; do **not** bash-source `.env`) | `scripts/run_amo_task_worker.sh` | n/a | openhome | via timer | journal | Amo task writes |
| openhome-planner-urls.timer | Hourly catch-up: PostMyPost planner links → live permalinks | oneshot → backfill | n/a | openhome | **INSTALLED, DISABLED** | `systemctl list-timers` | Would rewrite `post_url_*` in Notion |
| openhome-fb-daily-report.timer | Daily FB Groups/Marketplace report to error Telegram bot at 18:00 Bangkok | oneshot → `fb_daily_report.py --send` | n/a | openhome | **YES / enabled** | `systemctl list-timers` | Sends a digest, does not post |
| openhome-planner-urls.service | One-shot backfill (`--apply`) | `scripts/backfill_planner_urls.py --apply` | n/a | openhome | via timer | journal | Notion URL writes only |

Agent 5 Usher: **event-driven** after Agent 4 (`pending_postmypost_ai_agent`); no unit.  
Agent 8 Notary: **event-driven** from Agent 6; writes to `AGENT8_CONTRACTS_DIR`; no unit.  
Agent 9 connector: **not installed / not running**.  
CLIP curator (`re-curator`, `:8077`): **optional, not required** (`curator_fallback: true`); legacy unit **disabled**.

## Schedulers

| Source | Role | Duplicate risk |
|--------|------|----------------|
| `openhome-amo-task.timer` | amo task SLA every 10m | Do not also cron the same script; keep disabled until write approval |
| `openhome-planner-urls.timer` | planner→live URL catch-up every 1h | Do not also cron the same script; deferred sync already covers the first hour after a slot |
| Agent-internal APScheduler | in-process only when that unit runs | Start only one unit |

## Singleton safety

- File/flock or systemd `Type=simple` + single unit per watcher.
- Do **not** enable archive `re-*` units alongside `openhome-*`.
- Wazzup webhook / FB inbox / Airbnb inbox: only one process when eventually enabled.

## Startup order (when operator approves live)

1. `/opt/openhome/runtime` + `.env` present (`chmod 600`, owner `openhome`)
2. `openhome-api` + `nginx` (already running)
3. `openhome-chain-watcher` (will process Notion-ready objects — can publish)
4. `openhome-agent6` (will answer real Telegram)
5. `openhome-amo-task.timer` (amo writes)
6. `openhome-agent1` / Wazzup webhook — separate approval (Agent 1 polls Telegram)

## Intentionally not auto-started

Agent1 bot, Agent6 userbot, chain watcher, amo-task timer, WhatsApp UI sync, Agent9/10 daemons, amo custom channel connect, FB/Airbnb source-native send.

## Persistence env (production)

| name | value |
|------|--------|
| `AGENT2_ROOT` | `/opt/openhome/app/agent_2_registrar/_import/assistant-media` |
| `FB_PARSER_ROOT` | `/opt/openhome/app/agent_1_parser/fb_parser` |
| `FB_PARSER_PYTHON` | `/opt/openhome/app/agent_1_parser/fb_parser/.venv311/bin/python` |
| `PUBLISHER_LEDGER_PATH` | `/opt/openhome/runtime/publisher/publications.sqlite3` |
| `PUBLISHER_PMP_STATE_PATH` | `/opt/openhome/runtime/publisher/postmypost_publications.json` |
| `TG_SESSION_DIR` | `/opt/openhome/runtime/sessions` |
| `AGENT8_CONTRACTS_DIR` | `/opt/openhome/runtime/contracts` |
| `AGENT6_SESSION_STORE_DIR` | `/opt/openhome/runtime/state/qualifier_sessions` |
| `AGENT7_OWNERS_PATH` | `/opt/openhome/runtime/state/owners.json` |
| `AMO_TASK_STATE_PATH` | `/opt/openhome/runtime/state/amo_task_state.json` |
| `AGENT1_USERS_FILE` | `/opt/openhome/runtime/state/agent1_users.txt` |
| `AGENT5_STATE_PATH` | `/opt/openhome/runtime/state/usher_postmypost_ai_agent.json` |

## Night-prep / persistence facts (no secrets)

- Higgsfield **API keys present**; **CLI OAuth missing**. Auto provider falls back to Platform API (DoP 1-photo). Seedance 2.0 multi-ref needs `higgsfield auth login` as `openhome`.
- `VIDEO_ENGINE` unset: per-object Notion «Видео-движок» is the production selector. Wan needs `FAL_KEY` (missing) — not enabled.
- Wazzup channel state `notenoughmoney` — external billing; send/webhook OFF
- Facebook Agent7 profile: unchanged. Airbnb: LOGIN REQUIRED (untouched)
- Production sqlite / Telethon sessions / contracts live under `/opt/openhome/runtime` (not under replaceable app tree)

## Legacy (`/opt/real-estate`)

| Unit | State | Action taken |
|------|-------|----------------|
| `re-agent1-bot` | disabled / inactive | left disabled (points at archive tree) |
| `re-agent6-qualifier` | disabled / inactive | left disabled |
| `re-chain-watcher` | disabled / inactive | left disabled; replacement is `openhome-chain-watcher` |
| `re-curator` | **disabled** | CLIP optional |
