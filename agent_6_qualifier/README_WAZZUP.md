# Agent 6 Qualifier — WhatsApp via Wazzup

## Current stage

- Wazzup channel connected externally (WhatsApp Business)
- amoCRM integration connected externally via Wazzup
- API adapter ready (local)
- Read-only diagnostic ready
- Webhook receiver + **qualification dry-run** ready
- Send disabled
- Auto reply disabled

Telegram transport (Telethon userbot) is unchanged.

## Architecture

```
WhatsApp Business App
↕
Wazzup
↕
amoCRM
↕
Agent 6 (inbound → shared Qualifier → DRY-RUN reply — no POST)
```

## Webhook

Route: `POST /webhooks/wazzup`  
Module: `agent6_qualifier.messaging.webhook_http`

Local serve (localhost only):

```bash
# keep send/auto_reply false
WAZZUP_WEBHOOK_ENABLED=true PYTHONPATH=src python3 scripts/wazzup_webhook_serve.py
```

Local simulate (no tunnel needed):

```bash
PYTHONPATH=src python3 scripts/wazzup_inbound_simulate.py
```

### Public HTTPS

This project has **no** built-in public domain / ngrok / cloudflared.
Real Wazzup delivery needs a temporary HTTPS tunnel — **do not start one without confirmation**.

### Wazzup CRM API v3 settings (manual)

Do **not** change Wazzup via API from Agent 6.

In Wazzup / API webhook settings set:

- **Webhook URL:** `https://<YOUR_PUBLIC_HOST>/webhooks/wazzup`
- **Subscriptions:** `messagesAndStatuses=true` only  
  (`contactsAndDealsCreation=false`, `channelsUpdates=false`, `templateStatus=false`)

Wazzup verifies the URL with `POST {"test": true}` — our handler returns 200.

Leave `WAZZUP_WEBHOOK_BEARER` empty unless you terminate TLS with a reverse proxy that injects a shared secret (Wazzup CRM v3 does not require a custom Authorization header on webhook delivery).

Payload shape: `{ "messages": [ ... ] }` (not a separate `message.add` envelope in CRM v3).

## Future flow

```
Inbound WhatsApp → Wazzup webhook → Agent 6 → qualification
→ amoCRM state update → Wazzup send → client
```

Human outbound not from Agent 6 → `HUMAN_HANDOFF` → bot stops.

## Precondition before auto-reply

Before `WAZZUP_AUTO_REPLY_ENABLED=true`, disable the existing Wazzup/amoCRM
autoresponse (“Hello! Thank you for contacting us. We'll get back to you soon.”).

## Modules

| Path | Role |
|------|------|
| `messaging/types.py` | `ClientMessagingTransport`, canonical inbound |
| `messaging/wazzup_client.py` | GET channels / POST message (blocked by default) |
| `messaging/wazzup_transport.py` | Wazzup + Telegram marker transports |
| `messaging/wazzup_inbound.py` | webhook → canonical model |
| `messaging/ownership.py` | BOT_ACTIVE / HUMAN_HANDOFF / PAUSED / CLOSED |
| `messaging/inbound_dry_run.py` | shared Qualifier → proposed reply, no POST |
| `messaging/webhook.py` / `webhook_http.py` | `POST /webhooks/wazzup` |
| `messaging/idempotency.py` | inbound/outbound dedup |
| `scripts/wazzup_diagnose.py` | read-only GET diagnostic |
| `scripts/wazzup_inbound_simulate.py` | local inbound dry-run |
| `scripts/wazzup_webhook_serve.py` | localhost webhook server |
