# FULL LOCAL E2E READINESS

**Project:** `/Users/lifefmg/Desktop/Агенты/Real Estate Agent - refactor`  
**Audit date:** 2026-08-10 (local)  
**Mode:** READ-ONLY audit — no live sends, publishes, amo/Notion mutations, owner outreach, Meta writes.  
**GitHub / push:** NOT used.

---

# 1. Executive Verdict

**PARTIALLY READY**

Core CRM + Agent 6/7/8 business logic + Wazzup transport + PostMyPost config + Notion/amoCRM auth are largely in place.  
A **full live** end-to-end (property → publish → client → owner → booking → contract) is **not go** yet because of controlled live gates, WhatsApp parity gaps, pending Agent 5 network trigger, stale backlogs, and temporary tunnel fragility.

| Area | Verdict |
|------|---------|
| Property input (FB / Airbnb parsers) | READY (code + creds mostly; Drive OAuth missing for Airbnb photos path) |
| Notion persistence | READY |
| Content / video (Agent 3) | READY (creds SET; not validated live) |
| Publication PostMyPost (Agent 4) | READY (token + project + accounts) — live publish NOT run |
| Phone FB Groups/Marketplace | DEGRADED (pending jobs + device-dependent) |
| Client TG Agent 6 | READY (sessions present; live not started) |
| Client WA Agent 6 | PARTIAL (shared core yes; media/doc deferred; live flags off; autoresponse unresolved) |
| Owner Agent 7 | PARTIAL (TG auto; WA only controlled allowlist; live off) |
| Booking Agent 8 | TEST CONTRACT READY (booking request docx); full rental contract NOT IMPLEMENTED |
| Agent 5 Usher | OPTIONAL / NOT READY for network AI-agent trigger |
| Agent 9 FB owner connector | DEGRADED (code + profile; not required for min E2E) |
| Agent 10 Meta Ads | OUT OF SCOPE for core E2E; write flags false |

---

# 2. Full Architecture Map

```
PROPERTY URL
  ├─ Agent 1A Airbnb parser (TG bot / CLI) ─► session + Notion/Drive/R2
  └─ Agent 1B FB Marketplace parser (TG bot / CLI) ─► session photos/description
        └─ Agent 2 Registrar ─► Notion CRM + Cloudflare R2
              └─ Agent 3 Director ─► video (Higgsfield/Seedance) + R2
                    └─ Agent 4 Publisher ─► Telegram + PostMyPost (IG/FB/TikTok/…)
                    └─ Agent 4 Social (phone ADB) ─► FB Groups / FB Marketplace
                          └─ Agent 5 Usher ─► PostMyPost AI Agent queue (pending network)
LEAD INBOUND
  ├─ Telegram userbot (Agent 6)
  └─ WhatsApp via Wazzup webhook ─► shared process_client_message
        ├─ amoCRM pipeline «Аренда — лиды»
        ├─ matching / Notion listings
        ├─ Agent 7 Envoy (owner check)
        └─ Agent 8 Notary (booking docx)
PARALLEL / OPTIONAL
  ├─ contact_role dual sync (amo + WA UI lists)
  ├─ whatsapp_ui_sync (Playwright mirror lists)
  ├─ Agent 9 Connector (FB Marketplace owner messaging)
  └─ Agent 10 Meta Ads (disabled writes)
```

**Shared packages:** `contact_role/`, `whatsapp_ui_sync/`, `schema/`, `config/`, root `scripts/`.  
**Agent 7/8 folders** `agent_7_envoy/`, `agent_8_notary/` are README shims; runtime lives in `agent_6_qualifier/src/agent7_envoy` and `agent8_notary`.

---

# 3. Agent Matrix

| Agent | Path | Runtime location | Entrypoint(s) | Status for E2E |
|-------|------|------------------|---------------|----------------|
| 1A Airbnb | `agent_1_parser/airbnb_parser/` | same | `python3 main.py` (TG bot) | READY / Drive OAuth MISSING |
| 1B FB MP | `agent_1_parser/fb_parser/` | same | `agent1b/fb_parser.py`, TG bot | READY (storage_state exists) |
| 2 Registrar | `agent_2_registrar/` | same (+ `_import/assistant-media`) | chain / OpenClaw workspaces | READY |
| 3 Director | `agent_3_director/` | same | video pipeline scripts | READY (creds SET) |
| 4 Publisher | `agent_4_publisher/` | same | `scripts/publisher_run.py`, `publish_pipeline.py` | READY (PostMyPost) |
| 4 Social | `agent_4_publisher_social/` | phone ADB | job runner | DEGRADED / device |
| 5 Usher | `agent_5_usher/` | same | called from Agent 4 | OPTIONAL — network trigger pending |
| 6 Qualifier | `agent_6_qualifier/` | `src/agent6_qualifier` | `scripts/start_userbot.sh`, Wazzup webhook serve | PARTIAL WA / READY TG code |
| 7 Envoy | shim `agent_7_envoy/` | `agent_6_qualifier/src/agent7_envoy` | auto from Qualifier; `agent8_run.py` | PARTIAL |
| 8 Notary | shim `agent_8_notary/` | `agent_6_qualifier/src/agent8_notary` | on `booking_confirmed` | TEST CONTRACT READY |
| 9 Connector | `Агент 9/` | isolated | Notion poll + Playwright FB | DEGRADED / optional |
| 10 Meta Ads | `Агент 10/` | isolated | diagnostics / future ads | NOT part of core E2E |

---

# 4. Integration Matrix

| Integration | Purpose | Auth | Config | Read check | Write path | Status | Blocker |
|-------------|---------|------|--------|------------|------------|--------|---------|
| Notion | Object CRM | Token SET | DB SET | DB OK, 81 cols, schema OK | Agents 1–4,6–9 | READY | planned publisher_* cols only warnings |
| amoCRM | Leads pipeline | Token + subdomain SET | Pipeline stages match code | Account OK; pipeline «Аренда — лиды» | Agent 6–8 | READY | live mutations off for this audit |
| Wazzup | WA transport | API key SET | channel `7ac4092a-…`, plainId `66625124002` | channels active; webhook 200 | Agent 6/7 WA | READY (guards off) | SEND/AUTO_REPLY false; autoresponse unresolved |
| Telegram Telethon | Client/owner | API_ID/HASH/SESSION SET; `.session` files exist | `TG_SESSION` | files present (no live connect) | Agent 6/7 | AUTH LIKELY READY | live connect not verified this audit |
| PostMyPost | Social publish | `POSTMYPOST_API_TOKEN` SET | project `355063`, accounts mapped | `list_postmypost_accounts.py` OK | Agent 4 | READY | live create not run |
| Gemini | Agent 6 brain + parsers | `GEMINI_API_KEY` SET | model SET | present | Agent 6/1/3/9 | READY | — |
| Anthropic | Airbnb descriptions/tasks | SET | — | present | Agent 1A | READY | optional for FB path |
| Cloudflare R2 | Media storage | keys SET | bucket/endpoint SET | config only | Agent 2/3 | READY | live upload not run |
| Higgsfield | Video | keys SET | — | config only | Agent 3 | READY | live not run |
| Google Drive | Airbnb photos | `GOOGLE_CREDENTIALS_FILE` MISSING; no `credentials/` | example only | — | Agent 1A | BLOCKED for Drive path | OAuth setup needed |
| WhatsApp UI Playwright | Role list mirror | profile dir mostly unset in live `.env` | flags dry-run | Playwright import OK | contact_role | DEGRADED / AUTH_REQUIRED likely | secondary |
| Meta Ads | Paid promo | token SET; writes false | account IDs SET | flags false | Agent 10 | OUT OF SCOPE | — |
| Metricool | Legacy publish | tokens SET | `enabled: false` | — | Agent 4 | DISABLED | — |
| ChatPlace | Legacy funnel | key SET | `enabled: false` | — | Agent 4 | DISABLED | — |
| Canva | Design | — | — | no real integration (false “canvas” hit only) | — | NOT IMPLEMENTED | — |
| Airbnb API | Official API | — | — | none | — | NOT IMPLEMENTED | browser scrape only |
| amoCRM MCP | Optional MCP server | example only | port 8787 | — | tooling | OPTIONAL | — |

---

# 5. Env Matrix

**Env files found:** 10 live `.env` + 11 `.env.example` (21 total). **~224 unique keys.**

Legend for CURRENT STATE: `SET` / `EMPTY` / `MISSING` (never print values).

### Critical / blocking for core E2E

| VARIABLE | USED BY | REQUIRED FOR | STATE | SECRET | BLOCKING |
|----------|---------|--------------|-------|--------|----------|
| NOTION_API_KEY / NOTION_TOKEN / NOTION_DB_ID | multi | CRM objects | SET | mix | YES |
| AMO_ACCESS_TOKEN / AMO_SUBDOMAIN | Agent 6 | CRM deals | SET | mix | YES |
| WAZZUP_API_KEY / WAZZUP_CHANNEL_ID | Agent 6 WA | WA transport | SET | mix | YES (WA path) |
| TG_API_ID / TG_API_HASH / TG_SESSION | Agent 6/7 | TG userbot | SET | SECRET | YES (TG path) |
| GEMINI_API_KEY | Agent 6 brain | qualification NLP | SET | SECRET | YES |
| POSTMYPOST_API_TOKEN | Agent 4 | social publish | SET | SECRET | for publish leg |
| CLOUDFLARE_* R2 | Agent 2/3 | media | SET | mix | for media leg |
| WAZZUP_SEND_ENABLED | WA | live send | SET=`false` | no | gate |
| WAZZUP_AUTO_REPLY_ENABLED | WA | live bot replies | SET=`false` | no | gate |
| AGENT7_LIVE_OUTREACH_ENABLED | Agent 7 | owner outbound | SET=`false` | no | gate |
| AGENT7_CONTROLLED_OWNER_PHONES | Agent 7 WA | safe owner | MISSING/empty | no | YES for WA owner |
| GOOGLE_CREDENTIALS_FILE / DRIVE_ROOT_FOLDER_ID | Airbnb | Drive photos | MISSING | yes | Airbnb Drive path |
| OPENAI_API_KEY | some FB backends | scrapegraph | MISSING | yes | optional backend |
| WHATSAPP_UI_PROFILE_DIR | WA UI sync | list mirror | MISSING in live | no | optional |
| META_*_ENABLED | Agent 10 | ads | SET=`false` | no | N/A core E2E |

### Stage / safety flags (current)

| Flag | Current (Agent6 `.env`) |
|------|-------------------------|
| WAZZUP_WEBHOOK_ENABLED | true (listener) |
| WAZZUP_SEND_ENABLED | false |
| WAZZUP_AUTO_REPLY_ENABLED | false |
| WAZZUP_LIVE_ALLOWLIST_ENABLED | true |
| WAZZUP_LIVE_ALLOWLIST_PHONES | SET (1 phone) |
| WAZZUP_STAGE_MODE | true |
| CONTACT_ROLE_* sync | false / dry-run true |
| WHATSAPP_UI_SYNC_ENABLED | false |
| WHATSAPP_UI_DRY_RUN | true |

Full key inventory was generated during audit (`SET/EMPTY/MISSING` only); secrets never logged.

---

# 6. Authorization Matrix

| System | Status | Notes |
|--------|--------|-------|
| Notion | AUTH READY | DB «Аренда недвижимости» readable; schema validate OK |
| amoCRM | AUTH READY | Account «Ai real estate»; pipelines readable |
| Wazzup | AUTH READY | Channel active; GET webhooks/channels OK |
| Telegram | SESSION FILES PRESENT | Live Telethon auth not exercised (no connect/send) |
| PostMyPost | AUTH READY | Accounts/project listed successfully |
| Gemini | KEY SET | No live generate call in this audit |
| R2 / Higgsfield | KEYS SET | No live upload |
| Google Drive | TOKEN MISSING | No credentials directory |
| WhatsApp Web UI | AUTH_REQUIRED / DEGRADED | Profile path not configured in live env |
| FB parser browser | SESSION PRESENT | `fb_storage_state.json` exists |
| Agent 9 FB profile | PROFILE DIR EXISTS | Playwright profile under `Агент 9/data/facebook_profile/` |
| Meta Ads | TOKEN SET; WRITE DISABLED | Correct for core E2E |

---

# 7. Property Input Sources

| Source | Support | Evidence |
|--------|---------|----------|
| Facebook Marketplace URL | SUPPORTED | `agent_1_parser/fb_parser` |
| Airbnb listing URL | SUPPORTED | `agent_1_parser/airbnb_parser` (browser scrape, not API) |
| Manual / Notion direct | PARTIAL | Can seed Notion manually; not primary UX |
| Facebook Page post as listing source | NOT SUPPORTED as parser input | Publication target ≠ ingestion |
| Other listing sites | NOT SUPPORTED | — |

**Production entrypoints (actual):**

1. **Recommended FB path:** Telegram bot / CLI → `agent_1_parser/fb_parser` → Agent 2 session handoff → Notion.  
2. **Airbnb path:** `cd agent_1_parser/airbnb_parser && python3 main.py` (TG bot).  
3. **Existing object shortcut for client E2E:** start from object already in Notion + WA/TG inbound (skips ingest).

---

# 8. Publication Channels

| Channel | Status | Notes |
|---------|--------|-------|
| Instagram Reel | READY | PostMyPost account mapped; config delay |
| Instagram Post/Carousel | READY | capabilities in publisher.json |
| Facebook Page Post | READY | account id mapped |
| Facebook Reel | PARTIAL / via IG+FB PostMyPost capabilities | confirm product type at publish time |
| TikTok | READY (config) | account mapped |
| YouTube | READY (config) | account mapped |
| LinkedIn / X / Threads | READY (config) | accounts mapped |
| Telegram channel | READY | separate publisher scripts |
| FB Groups (phone) | DEGRADED | pending jobs; ADB device |
| FB Marketplace listing publish (phone) | DEGRADED | pending jobs |
| Metricool | DISABLED | `enabled: false` |
| ChatPlace funnels | DISABLED | `enabled: false` |

---

# 9. Client Channels

| Channel | Status | Notes |
|---------|--------|-------|
| Telegram (Telethon userbot) | READY (code+session) | `start_userbot.sh` |
| WhatsApp (Wazzup) | PARTIAL | shared core wired; live send gated; media/doc deferred |
| Social comment/DM → Agent 6 | NOT READY | Agent 5 inbound contract only; no webhook |

---

# 10. Owner Channels

| Channel | Status | Notes |
|---------|--------|-------|
| Telegram owner | READY (code) | auto_outreach TG path |
| WhatsApp owner | PARTIAL | implemented with `AGENT7_CONTROLLED_OWNER_PHONES` gate |
| Airbnb DM | NOT auto | manager alert / manual |
| FB Marketplace DM (Agent 7) | NOT auto | manager alert |
| Agent 9 FB Messenger | SEPARATE service | DEGRADED/optional |

**OWNER OUTREACH LIVE READY:** **PARTIAL** (TG yes in code; WA only controlled; live flag false; no controlled owner phone configured).

---

# 11. amoCRM Readiness

- Auth: READY  
- Account: Ai real estate  
- Target pipeline: **Аренда — лиды** (`11088150`)  
- Stages present (match Agent 6 code names):

| Stage | ID |
|-------|-----|
| Неразобранное | 87078550 |
| Новый лид | 87078554 |
| Квалификация | 87078558 |
| Подбор | 87078562 |
| Запрос владельцу | 87078566 |
| Согласование условий | 87078570 |
| Бронь подтверждена | 87078574 |
| Успешно реализовано | 142 |
| Закрыто и не реализовано | 143 |

- Contact role field «Тип контакта» (`937587`) + tags CLIENT/OWNER/AGENT: schema cached in `contact_role/data/amocrm_contact_role_schema.json`  
- Second pipeline «Воронка» exists — Agent 6 uses rental pipeline via `ensure_pipeline()`.

---

# 12. Notion Readiness

- DB accessible: YES («Аренда недвижимости»)  
- Columns: 81; `schema/validate_schema.py`: **OK** (0 missing, 0 type mismatch)  
- Warnings: planned `publisher_*` columns not created (non-blocking; legacy `agent6_*` publisher fields exist)  
- Critical fields present: Объект ID, owner/WA/TG contacts, Агент/Владелец (тип), Календарь, availability_*, Фото, source URL, publication URLs, prices, districts, etc.  
- **No schema changes made.**

---

# 13. Wazzup Readiness

| Check | Result |
|-------|--------|
| API connected | YES |
| channel_id `7ac4092a-cf3c-450f-8518-d7cc7bd3f995` | YES |
| plainId `66625124002` | YES |
| state active | YES |
| webhook URI | `https://tue-boulder-mason-nova.trycloudflare.com/webhooks/wazzup` |
| public test ping | HTTP 200 |
| local `:8765` test ping | HTTP 200 |
| SEND / AUTO_REPLY | false / false (safe) |
| Allowlist | enabled, 1 phone |
| Autoresponse greeting | **NOT RESOLVED VIA API** (docs: disable in Wazzup/WA Business/amo before AUTO_REPLY) |

Webhook was **not changed** during this audit (only GET).

---

# 14. Telegram Readiness

| Item | Status |
|------|--------|
| API_ID / API_HASH | SET |
| Session name | SET |
| `official_company.session` | EXISTS |
| `agent7_userbot.session` (+ script variants) | EXISTS |
| Userbot entry | `scripts/start_userbot.sh` → `agent6_qualifier.tg_userbot` |
| Live connect/send | NOT verified (audit constraint) |

---

# 15. PostMyPost Readiness

| Item | Status |
|------|--------|
| Token | SET |
| Project | Open Home / id `355063` |
| Accounts | IG, FB, TikTok, YT, LinkedIn, X, Threads mapped |
| Metricool | disabled |
| Deferred URL sync | scripts + `data/deferred_sync/` logs exist |
| Publication ledger SQLite | exists; `publications` count **0** |

---

# 16. Airbnb Readiness

| Question | Answer |
|----------|--------|
| AIRBNB SOURCE URL INPUT | **YES** |
| AIRBNB SCRAPER / INGESTION | **YES** (Playwright/browser parser) |
| AIRBNB OWNER CONTACT EXTRACTION | **YES** (`owner_detect.py`) |
| AIRBNB PUBLICATION | **NO** (not a publish target) |
| AIRBNB API | **NO** |
| AIRBNB BROWSER SESSION | **YES** (parser browser) |
| Google Drive photos path | **BLOCKED** (credentials missing) |
| Calendar pre-check for Agent 7 | **YES** (Notion «Календарь» / airbnb_check) |

---

# 17. Facebook Readiness

| Surface | Status |
|---------|--------|
| Marketplace **ingestion** (Agent 1B) | READY |
| Page publish via PostMyPost | READY (config) |
| Groups / Marketplace **publish** via phone | DEGRADED (pending jobs, device) |
| Agent 9 owner Messenger outreach | DEGRADED / optional |
| Meta Ads | Disabled writes; out of core E2E |

---

# 18. Agent6 Full Flow

| Capability | Telegram | WhatsApp |
|------------|----------|----------|
| process_client_message | YES | YES (shared) |
| Qualifier + SessionStore | YES | YES (`wa_<E164>.json`) |
| templates/prompts | YES | YES (same) |
| Gemini NLP | YES | YES |
| amoCRM | YES | YES (resolve existing first) |
| matching / Notion | YES | YES |
| need_owner_check → Agent 7 | YES | YES (gated) |
| booking → Agent 8 | YES (send file TG) | PARTIAL (generate/attach; **WA file send deferred**) |
| human handoff | YES | YES (ownership + crmMessageId) |
| Wazzup send path | N/A | YES (guarded) |

**WHATSAPP PARITY WITH TELEGRAM:** **PARTIAL**  
Gaps: media/doc outbound, Agent7 WA requires controlled owner, autoresponse collision risk, UI list sync secondary.

---

# 19. Agent7 Full Flow

| Item | Status |
|------|--------|
| Contact source | Notion listing owner_whatsapp / telegram / channel priority |
| Telegram transport | Implemented auto-send |
| WhatsApp transport | Implemented + controlled allowlist gate |
| Templates | `owner_first_message` / ACK / busy followup |
| Calendar pre-check | Implemented |
| Owner reply handling | TG yes; WA owner inbound routing added |
| Role hook | CONTACT_OUTREACH_STARTED |
| Live gate | `AGENT7_LIVE_OUTREACH_ENABLED=false` |

**OWNER OUTREACH LIVE READY:** **PARTIAL / NO for unrestricted live** (safe only with controlled owner + flag on).

---

# 20. Agent8 Booking/Contract

| Item | Status |
|------|--------|
| Booking request docx | IMPLEMENTED (`booking_doc.py` + Node generator) |
| Attach to amo | IMPLEMENTED |
| Send to TG client | IMPLEMENTED |
| Send to WA client | DEFERRED (text parity V1) |
| Full rental / payment contract | **NOT IMPLEMENTED** |

**REAL CONTRACT READY:** NO (full lease)  
**TEST CONTRACT READY:** YES (booking agreement)  
**BLOCKED:** full notary lease path

---

# 21. Local Processes / Ports

| Service | Host | Port | Now |
|---------|------|------|-----|
| Wazzup webhook | 127.0.0.1 | **8765** | LISTENING |
| cloudflared metrics | 127.0.0.1 | 20241 | running (quick tunnel) |
| amoCRM MCP (optional) | 127.0.0.1 | 8787 | not required |
| Telegram userbot | n/a | — | not running |
| Agent 4 poller | n/a | — | not running |
| WA UI queue worker | n/a | — | not running |

**Tunnel:** temporary trycloudflare URL — **dies on restart**; not persistent named tunnel.

---

# 22. Queues / Backlog

| Queue | Count / state | Risk if live launch |
|-------|---------------|---------------------|
| Airbnb pricing_queue | 0 jobs | low |
| Agent 9 queue.json | empty | low |
| contact_role outbox (live-test) | 3 (DONE/SKIPPED) | low |
| contact_role outbox (`data/`) | 28 (14 FAILED, 14 SKIPPED) | **do not auto-replay** |
| Agent 4 publications ledger | 0 rows | low |
| Agent 4 chatplace_jobs | 1 leftover IG job file | ignore (ChatPlace disabled) |
| Agent 4 social phone jobs | 3 with pending fb_groups/fb_marketplace | **do not auto-run phone publisher** |
| montage.lock | holds `A_20260807_001` | stale lock risk for that object |
| publisher locks (social) | empty lock files | check before phone run |
| Agent 6 WA sessions | 2 test sessions (`wa_66625124002`, `wa_66987654321`) | reset before controlled test |
| owners.json | TG test owners only | no WA controlled owner |

---

# 23. Live Side Effects Matrix

| ACTION | SERVICE | CAN HAPPEN LIVE? | CURRENT GUARD | RISK |
|--------|---------|------------------|---------------|------|
| Send WhatsApp | Wazzup | only if SEND+AUTO+allowlist | both false | high if armed |
| Send Telegram | Telethon | if userbot running | process off | high |
| Publish IG/FB/… | PostMyPost | if publisher run | manual | high |
| Publish FB Groups/MP | phone ADB | if social jobs run | pending jobs | high |
| Update amo | Agent 6/7/8 | on message processing | webhook can process business even if send dry | medium |
| Write Notion | many agents | on pipelines | not running | medium |
| Owner outreach | Agent 7 | live flag false | + controlled WA list | high |
| Booking doc | Agent 8 | on booking_confirmed | local file + amo attach | medium |
| Meta ads write | Agent 10 | no | META_* false | low |
| WA UI list edits | Playwright | sync disabled / dry-run | flags | medium |

---

# 24. Missing Data

- Controlled **OWNER** WhatsApp test contact (and Notion object whose `WhatsApp контакт` points to it)  
- Clean Agent 6 session for test client phone (prepare script exists; client `wa_66625124001` was reset earlier)  
- Confirmation that Wazzup/WA Business greeting autoresponse is OFF  
- Optional: persistent cloudflared named tunnel  
- Google Drive credentials for Airbnb photo path  
- WhatsApp UI persistent profile configuration for list mirror

---

# 25. Missing Credentials

| Credential | State | Needed for |
|------------|-------|------------|
| Google Drive OAuth / service account | MISSING | Airbnb → Drive |
| OPENAI_API_KEY | MISSING | optional FB scrapegraph backend |
| WHATSAPP_UI_PROFILE_DIR (+ related) | MISSING in live | WA list mirror |
| AGENT7_CONTROLLED_OWNER_PHONES | empty | WA owner live |
| META_APP_SECRET | EMPTY | Agent 10 only |
| Agent10 Notion token/db | EMPTY | Agent 10 only |

---

# 26. Missing Authorizations

**AUTH REQUIRED NOW (for full live E2E):**

1. User confirm Wazzup greeting autoresponse disabled  
2. Controlled owner phone authorization for Agent 7 WA  
3. Explicit arm of SEND/AUTO_REPLY/AGENT7 flags (not done)

**AUTH READY:** Notion, amoCRM, Wazzup API, PostMyPost, Gemini key present, TG session files, FB parser storage_state, R2/Higgsfield keys.

**AUTH OPTIONAL:** WhatsApp UI mirror, Google Drive, Agent 9 FB, Meta Ads, Canva (N/A), Metricool/ChatPlace (disabled).

---

# 27. Critical Blockers

1. **No controlled owner contact** → cannot safely complete owner→booking leg on WhatsApp.  
2. **Autoresponse collision unresolved** → cannot safely enable `WAZZUP_AUTO_REPLY_ENABLED`.  
3. **Live send flags intentionally OFF** (correct for safety; must arm deliberately).  
4. **WhatsApp booking file send deferred** → TG path needed for full media/doc parity OR accept text-only WA booking.  
5. **Temporary tunnel** → webhook URL fragile across restarts (must re-PATCH carefully when relaunching — not done in this audit).  
6. **Stale social/phone pending jobs + montage lock** → risk of unintended publishes if social publisher started blindly.

---

# 28. Non-Critical Blockers

- Agent 5 PostMyPost AI Agent network trigger pending  
- Agent 9 FB owner connector not required for min path  
- Agent 10 Meta Ads out of scope  
- Canva / Drive / Metricool / ChatPlace  
- WhatsApp UI list sync secondary  
- Planned Notion `publisher_*` columns  
- Agent 9 deploy test failure (`test_start_script_entrypoint`) — isolated

---

# 29. Minimum Full E2E Path Available Now

**Minimum true business path (when live gates deliberately armed):**

1. Use **existing Notion object** (skip ingest) **or** FB Marketplace → Agent 1B → Agent 2 → Notion  
2. Optional: skip video/publish if object already has links  
3. WhatsApp client allowlisted phone → Agent 6 shared core → amo stages  
4. Matching → `need_owner_check`  
5. **STOP unless** controlled owner phone + Agent7 live + object WA contact match  
6. Owner reply → client continuation → booking_confirmed  
7. Agent 8 booking docx (+ amo attach); WA client gets text note / TG gets file  

**If owner only on Telegram:** run TG userbot for Agent 7 leg (hybrid).

Airbnb-full + Drive is **not** required for this minimum.

---

# 30. Maximum Full E2E Path Available Now

```
Airbnb or FB URL
→ Agent 1 parse
→ Agent 2 Notion + R2 (Airbnb Drive optional/blocked)
→ Agent 3 video
→ Agent 4 PostMyPost (IG Reel + FB + others) + Telegram
→ (optional phone FB Groups/MP)
→ Lead via WA and/or TG
→ Agent 6 qualification + amo full stage progression
→ Agent 7 owner (TG auto and/or controlled WA)
→ booking_confirmed
→ Agent 8 booking agreement
→ (Agent 5 / Agent 9 / Agent 10 still optional or disabled)
```

---

# 31. Exact Startup Order

**Do not start live yet.** Planned order:

1. Clear/quarantine stale phone jobs + montage.lock review  
2. Confirm autoresponse OFF  
3. Set allowlist client (+ owner if WA)  
4. Start `wazzup_webhook_serve.py` (8765)  
5. Start **persistent** tunnel OR refresh trycloudflare and PATCH Wazzup webhook URI  
6. Start Telegram userbot (`start_userbot.sh`) if TG/owner-TG needed  
7. Arm SEND/AUTO_REPLY only after (2)  
8. Arm AGENT7_LIVE + CONTROLLED_OWNER only after owner identity confirmed  
9. Optional: Agent 4 publisher only when publishing new object  
10. Optional: contact_role / WA UI sync  
11. Run E2E watcher `scripts/whatsapp_agent6_full_e2e_test.py`  
12. After test: disarm all live flags

---

# 32. Exact User Actions Required

1. Confirm: `AUTORESPONSE_OFF: yes`  
2. Provide: controlled `OWNER_PHONE` (or accept STOP at owner)  
3. Confirm test CLIENT phone (default allowlist already has one controlled number)  
4. Choose object: existing Notion ID **or** new FB/Airbnb URL  
5. Decide channel for owner: WA controlled vs TG username on listing  
6. Decide whether booking doc via TG is acceptable for “full” or require WA file send fix first  
7. Optional: run `setup_drive_oauth.py` if Airbnb Drive path needed  
8. Optional: configure WhatsApp UI profile for list mirror

---

# 33. Proposed Unified Launcher

**Exists today:** NO single unified launcher (only per-agent scripts + `scripts/sync_env.py`, WA UI helpers).

**Proposal (do not implement in this audit):**

```
scripts/local_e2e_up.sh
  1) env guard (refuse if SEND without allowlist)
  2) webhook :8765
  3) tunnel (named preferred)
  4) tg_userbot
  5) optional publisher poller
  6) optional wa_ui_queue_worker
  7) healthchecks → readiness JSON
scripts/local_e2e_down.sh → disarm flags, stop processes
```

---

# 34. Tests

| Suite | Result |
|-------|--------|
| compileall key packages | OK |
| agent_6_qualifier pytest | **329 passed** |
| contact_role pytest | **77 passed** |
| whatsapp_ui_sync pytest | **23 passed** |
| agent_5_usher pytest | **3 passed** |
| Agent 10 pytest (non-live) | **83 passed**, 5 deselected |
| Agent 9 pytest (filtered) | **59 passed**, **1 failed** (`test_deploy.py::test_start_script_entrypoint`) |
| `git diff --check` | OK (no whitespace errors reported) |

No live integration sends/publishes executed.

---

# 35. Final Go/No-Go Checklist

| Check | Status |
|-------|--------|
| Notion auth + schema | GO |
| amoCRM auth + stages | GO |
| Wazzup channel | GO |
| PostMyPost config | GO |
| Agent 6 shared core WA+TG | GO (code) |
| Agent 7 controlled WA path | GO (code) / NO live |
| Agent 8 booking doc | GO (test contract) |
| Autoresponse resolved | **NO-GO** |
| Controlled owner | **NO-GO** |
| Live flags armed safely | **NO-GO** (intentionally) |
| WA media/doc parity | **NO-GO** for strict parity |
| Stale backlog quarantined | **NO-GO** until reviewed |
| Persistent webhook tunnel | **NO-GO** (temporary) |
| Meta ads interfering | GO (disabled) |
| Unified launcher | N/A |

**Overall launch decision:** **NO-GO for FULL LIVE E2E now** — **GO for continued preparation / controlled arming after user inputs.**

---

## Appendix A — Target flow step status

| Step | Status |
|------|--------|
| PROPERTY INPUT | READY |
| ingestion | READY (FB/Airbnb) |
| parsing/normalization | READY |
| object persistence (Notion/R2) | READY |
| content (captions/video) | READY / DEGRADED (Drive) |
| publication | READY (PostMyPost) / DEGRADED (phone) |
| lead inbound | READY TG / PARTIAL WA |
| Agent 6 qualification | READY |
| amoCRM | READY |
| matching | READY |
| owner availability | PARTIAL |
| Agent 7 | PARTIAL |
| owner response | PARTIAL |
| client continuation | READY (code) |
| booking | READY (logic) |
| Agent 8 notary/contract | TEST CONTRACT READY / full lease NOT IMPLEMENTED |

## Appendix B — Content / AI dependencies

| Dependency | Status |
|------------|--------|
| Gemini (Agent 6) | READY (key) |
| Anthropic | READY (key) |
| Higgsfield / Seedance | READY (keys) |
| Montage (assistant-media) | PRESENT; lock stale possible |
| Canva | NOT IMPLEMENTED |
| Google Drive | NOT READY (creds) |
| R2 galleries | READY (config) |

## Appendix C — Test identities

| Identity | Status |
|----------|--------|
| WA client allowlisted | PRESENT (1 phone in allowlist) |
| WA business channel plainId | `66625124002` |
| Controlled WA owner | **MISSING** |
| TG userbot sessions | PRESENT |
| TG test owner registry entries | PRESENT (dev/test usernames) |
| FB browser profiles | PRESENT (parser + Agent 9) |
