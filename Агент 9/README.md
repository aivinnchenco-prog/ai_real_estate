# Agent 9 — Connector

Isolated Facebook Marketplace owner outreach service.

## Architecture

```
Agents 1–8 → Notion ← Agent 9
```

Agent 9 has **no Python imports** from Agents 1–8. It reads/writes Notion independently.

### Hybrid dialogue design

- **Deterministic state machine** — business states, transitions, anti-duplicate
- **Gemini interpreter** — intent / question_type / role signals (structured JSON)
- **Policy engine** — decides allowed actions and facts; Gemini only phrases approved responses

`asyncio` tasks inside Agent 9 are unrelated to amoCRM tasks in Agent 6.

## Facebook Marketplace v1 scope

Agent 9:

1. Polls Notion for eligible Facebook listings
2. Opens Marketplace listing in isolated Playwright profile
3. Sends deterministic intro via Messenger
4. Collects WhatsApp (deterministic parser + validation)
5. Asks owner vs agent (deterministic)
6. Writes results to Notion

**Airbnb outreach is intentionally NOT implemented in v1.**

## Notion eligibility

- `Объект ID` prefix `F_` **or** `Источник объявления` is Facebook Marketplace URL
- Marketplace URL present (`Источник объявления` preferred, fallback `post_url_FB_marketplace`)
- `FB Outreach Status` not in `complete`, `declined`, `manual_review`, `failed`

## State machine

Business: `NEW → INTRO_SENT → WAITING_CONTACT → CONTACT_RECEIVED → ROLE_ASKED → WAITING_ROLE → COMPLETE`

Also: `DECLINED`, `MANUAL_REVIEW`

Browser (separate): `LOGIN_REQUIRED`, `LISTING`, `MESSAGE_BUTTON_AVAILABLE`, `MESSENGER_OPEN`, `MESSAGE_INPUT_READY`, `WAITING_REPLY`, `CHECKPOINT`, `ACCOUNT_PAUSED`, `UNKNOWN`

## Thread binding

`listing_id` extracted from `/marketplace/item/{id}` → after first send, Messenger `thread_url` captured → `thread_id` parsed from `/messages/t/{id}` → stored in local state + `FB Messenger Thread` in Notion.

## Setup

```bash
cd "Агент 9"
cp .env.example .env
pip install -r requirements.txt
playwright install chromium
python3 scripts/facebook_login.py   # manual login, profile in data/facebook_profile/
PYTHONPATH=src python3 -m agent9_connector.main
```

## Systemd

```bash
sudo bash deploy/install_agent9.sh
sudo systemctl start agent9-connector
```

Isolated service — failure does not affect Agents 1–8.

## Config

- `config/connector.json` — polling, rate limits, confidence thresholds
- `config/intro_templates.json` — deterministic intro + role question (ru/en)
- `.env` — `AGENT9_NOTION_*`, `AGENT9_GEMINI_*`

## Anti-hallucination

Policy never invents dates, guests, budget, nationality, pets, price, availability. Missing facts → clarify + return to WhatsApp/role goal.

## Anti-duplicate

`intro_sent_at` / `role_question_sent_at` persisted in `data/conversations.json`. Restart-safe.

## Rate limits

Configurable max outreach per hour/day. Jobs stay queued when limited.

## Facebook security

Login required / checkpoint / captcha → pause outreach, `manual_review`, no bypass.

## Tests

```bash
cd "Агент 9"
PYTHONPATH=src pytest tests/ -q
```

All offline — no production Notion/Gemini/Facebook calls.
