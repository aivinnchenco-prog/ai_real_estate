# Open Home production ENV inventory

Canonical secret file on VPS: `/opt/openhome/.env` (`chmod 600`, owner `openhome`).

Secret values are **never** listed here. `production present` reflects VPS after safe merge (2026-08-12).

Legend: **R**=required for intended core prod · **O**=optional/agent-specific · **S**=secret YES/NO · **P**=present on VPS YES/NO · **Class**=A production-required / B local-only / C obsolete / D dangerous live flag

## Live gates (forced OFF until explicit approval)

| name | R/O | class | S | default | P | notes |
|------|-----|-------|---|---------|---|-------|
| AGENT7_LIVE_OUTREACH_ENABLED | R | D | NO | false | YES | master Agent7 outbound |
| AGENT7_FACEBOOK_MESSENGER_ENABLED | R | D | NO | false | YES | source-native FB |
| AGENT7_AIRBNB_MESSAGES_ENABLED | R | D | NO | false | YES | source-native Airbnb |
| AMO_CHAT_WEBHOOK_ENABLED | R | D | NO | false | YES | manager→source webhook |
| AMO_CHAT_MIRROR_LIVE | R | D | NO | false | YES | amojo mirror |
| AMO_CHAT_CONNECT_LIVE | R | D | NO | false | YES | channel connect |
| WAZZUP_SEND_ENABLED | R | D | NO | false | YES | keep OFF until cutover approval |
| WAZZUP_AUTO_REPLY_ENABLED | R | D | NO | false | YES | |
| WAZZUP_WEBHOOK_ENABLED | R | D | NO | false | YES | |
| META_WRITE_ENABLED | O | D | NO | false | YES | Agent10 Meta writes |
| META_ACTIVE_ENABLED | O | D | NO | false | YES | |
| META_ADS_ENABLED | O | D | NO | false | YES | |
| PUBLISHER_SOCIAL_ENABLED | O | D | NO | false | YES | Agent4 social publish |
| FB_MARKETPLACE_PHONE_PUBLISHER_ENABLED | O | D | NO | false | YES | |
| FB_GROUPS_PHONE_PUBLISHER_ENABLED | O | D | NO | false | YES | |
| CONTACT_ROLE_AMO_SYNC_ENABLED | O | D | NO | false | YES | |
| CONTACT_ROLE_AUTO_ASSIGN_ENABLED | O | D | NO | false | YES | |

## amoCRM

| name | R/O | class | S | default | P |
|------|-----|-------|---|---------|---|
| AMO_SUBDOMAIN | R | A | NO | — | YES |
| AMO_ACCESS_TOKEN | R | A | YES | — | YES |
| AMO_DEFAULT_RESPONSIBLE_USER_ID | R | A | NO | — | YES |
| AMO_MANAGER_RESPONSIBLE_USER_ID | O | A | NO | — | YES |
| AMO_TASK_TYPE_ID | O | A | NO | — | YES |
| AMO_TASKS_ENABLED | O | A | NO | true | YES |
| AMO_CHAT_ACCOUNT_ID | O | A | NO | 33148394 | YES |
| AMO_CHAT_CLIENT_UUID | O | A | NO | — | YES |
| AMO_CHAT_AMOJO_BASE_URL | O | A | NO | amojo URL | YES |
| AMO_CHAT_PUBLIC_BASE_URL | R | A | NO | https://api.open-home.online | YES |
| AMO_CHAT_* channel ids/secrets | O | A | YES | empty until registration | PARTIAL |
| AMO_CHAT_WEBHOOK_HOST | R | A | NO | 127.0.0.1 | YES |
| AMO_CHAT_WEBHOOK_PORT | R | A | NO | 8000 | YES |
| AMO_CHAT_MIRROR_STORE_PATH | R | A | NO | /opt/openhome/runtime/stores/… | YES |
| AMO_CHAT_OWNER_SILENT | O | A | NO | — | YES |

## Wazzup / WhatsApp

| name | R/O | class | S | default | P |
|------|-----|-------|---|---------|---|
| WAZZUP_API_KEY | R | A | YES | — | YES |
| WAZZUP_CHANNEL_ID | R | A | NO | — | YES |
| WAZZUP_API_BASE_URL | R | A | NO | https://api.wazzup24.com | YES |
| WA_PROVIDER | R | A | NO | wazzup | YES |
| WAZZUP_STAGE_MODE | R | A | NO | true | YES |
| WAZZUP_LIVE_ALLOWLIST_ENABLED | R | A | NO | true | YES |
| WAZZUP_LIVE_ALLOWLIST_PHONES | O | A | NO | empty | YES |
| WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF | R | A | NO | true | YES |
| WAZZUP_REQUEST_TIMEOUT_SECONDS | O | A | NO | 15 | PARTIAL |
| WA_API_TOKEN / WA_INSTANCE_ID | O | B/C | YES | — | NO |

## Notion

| name | R/O | class | S | default | P |
|------|-----|-------|---|---------|---|
| NOTION_TOKEN | R | A | YES | — | YES |
| NOTION_DATABASE_ID | R | A | NO | — | YES |
| NOTION_API_KEY | O | A | YES | alias | PARTIAL |
| NOTION_DB_ID | O | A | NO | alias | PARTIAL |
| AGENT9_NOTION_TOKEN | O | A | YES | — | PARTIAL |
| AGENT9_NOTION_DATABASE_ID | O | A | NO | — | PARTIAL |
| AGENT10_NOTION_TOKEN | O | A | YES | — | PARTIAL |
| AGENT10_NOTION_DATABASE_ID | O | A | NO | — | PARTIAL |

## R2 / Cloudflare

| name | R/O | class | S | default | P |
|------|-----|-------|---|---------|---|
| CLOUDFLARE_ACCOUNT_ID | R | A | NO | — | YES |
| CLOUDFLARE_ACCESS_KEY_ID | R | A | YES | — | YES |
| CLOUDFLARE_SECRET_ACCESS_KEY | R | A | YES | — | YES |
| CLOUDFLARE_BUCKET | R | A | NO | — | YES |
| CLOUDFLARE_ENDPOINT | R | A | NO | — | YES |
| CLOUDFLARE_PUBLIC_BASE_URL | O | A | NO | — | YES |

## Model providers

| name | R/O | class | S | default | P |
|------|-----|-------|---|---------|---|
| GEMINI_API_KEY | R | A | YES | — | YES |
| GEMINI_MODEL | O | A | NO | — | YES |
| AGENT9_GEMINI_API_KEY | O | A | YES | — | YES |
| AGENT9_GEMINI_MODEL | O | A | NO | — | YES |
| AGENT10_LLM_API_KEY | O | A | YES | — | NO |
| AGENT10_LLM_PROVIDER / MODEL / ENABLED | O | A | NO | — | NO |
| ANTHROPIC_API_KEY | O | A | YES | — | PARTIAL |
| HIGGSFIELD_API_KEY / SECRET | O | A | YES | — | PARTIAL |

## Telegram

| name | R/O | class | S | default | P |
|------|-----|-------|---|---------|---|
| TG_API_ID | R | A | NO | — | YES |
| TG_API_HASH | R | A | YES | — | YES |
| TG_PHONE | R | A | NO | — | YES |
| TG_SESSION | R | A | NO | session name | YES |
| TG_EXPECTED_USER_ID / USERNAME | O | A | NO | — | YES |
| TG_BOT_TOKEN / TELEGRAM_BOT_TOKEN | O | A | YES | — | PARTIAL |
| TG_BOT_TOKEN_AGENT1 | O | A | YES | — | YES |
| TG_BOT_TOKEN_FB_PARSER | O | A | YES | — | PARTIAL |
| TG_BOT_TOKEN_PUBLISHER | O | A | YES | — | PARTIAL |
| ERROR_BOT_TOKEN / ERROR_CHAT_ID | O | A | YES | — | PARTIAL |

Sessions (files): `/opt/openhome/runtime/sessions/*.session` (`TG_SESSION_DIR`). Do not keep production sessions under `/opt/openhome/app`.

## Facebook / Airbnb / browser

| name | R/O | class | S | default | P |
|------|-----|-------|---|---------|---|
| AGENT7_BROWSER_PROFILES_DIR | R | A | NO | /opt/openhome/runtime/browser_profiles | YES |
| AGENT7_BROWSER_FAILURES_DIR | R | A | NO | …/agent7_browser_failures | YES |
| FB_BROWSER_PROFILE | R | A | NO | facebook_owner_outreach path | YES |
| AGENT7_FACEBOOK_PROFILE_DIR | R | A | NO | same | YES |
| AGENT7_AIRBNB_PROFILE_DIR | R | A | NO | airbnb_owner_outreach | YES (path set; profile MISSING) |
| AGENT7_OWNER_REQUEST_STORE_PATH | R | A | NO | runtime/stores | YES |
| FB_EMAIL / FB_PASSWORD | — | B | YES | — | NO (never on VPS) |

## Publisher / Meta / Metricool / Chatplace

| name | R/O | class | S | P |
|------|-----|-------|---|---|
| POSTMYPOST_* | O | A | YES | YES |
| CHATPLACE_* | O | A | YES | PARTIAL |
| META_* (read config) | O | A | mixed | YES |
| METRICOOL_* | O | A | YES | PARTIAL |
| GOOGLE_MAPS_API_KEY | O | A | YES | PARTIAL |

## Local-only (not migrated)

`VPS_*`, `CURSOR_API_KEY`, `FB_EMAIL`, `FB_PASSWORD`, UI dry-run paths, parser sleep/price batch tunables, WhatsApp UI-only vars — class **B**.

## Production path overrides

| name | value |
|------|--------|
| OPENHOME_API_PRODUCTION | true |
| OPENHOME_ENV_FILE | /opt/openhome/.env |
| AGENT2_ROOT | /opt/openhome/app/agent_2_registrar/_import/assistant-media |
| PUBLISHER_LEDGER_PATH | /opt/openhome/runtime/publisher/publications.sqlite3 |
| PUBLISHER_PMP_STATE_PATH | /opt/openhome/runtime/publisher/postmypost_publications.json |
| TG_SESSION_DIR | /opt/openhome/runtime/sessions |
| AGENT8_CONTRACTS_DIR | /opt/openhome/runtime/contracts |
| AGENT6_SESSION_STORE_DIR | /opt/openhome/runtime/state/qualifier_sessions |
| AGENT7_OWNERS_PATH | /opt/openhome/runtime/state/owners.json |
| AMO_TASK_STATE_PATH | /opt/openhome/runtime/state/amo_task_state.json |
| AGENT1_USERS_FILE | /opt/openhome/runtime/state/agent1_users.txt |
| AGENT5_STATE_PATH | /opt/openhome/runtime/state/usher_postmypost_ai_agent.json |
| AMO_CHAT_MIRROR_STORE_PATH | /opt/openhome/runtime/stores/… |
| AGENT7_OWNER_REQUEST_STORE_PATH | /opt/openhome/runtime/stores/… |
