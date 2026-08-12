# ARCHIVE VS REFACTOR GAP ANALYSIS

**Mode:** READ-ONLY comparison — no copy, merge, env changes, live actions, or GitHub/push.  
**Date:** 2026-08-10  
**Refactor:** `/Users/lifefmg/Desktop/Агенты/Real Estate Agent - refactor`  
**Primary archive:** `/Users/lifefmg/Desktop/Агенты/Real Estate Agent`

---

# Executive Summary

Refactor is **not a stripped-down loss of the core rental pipeline**. For Agents 6–8 and WhatsApp it is largely a **superset + structural cleanup**. The archive is the older sibling monorepo (last meaningful commit ~2026-08-05 publisher/PostMyPost era; README still described Metricool/ChatPlace/phone as primary).

**Overall:** refactor = **incomplete relative to “everything ever present in archive folders”**, but **complete-or-better for the documented core E2E** (parse → Notion → content → publish → qualify → owner → booking doc), with a few **valuable operational/legacy pieces** left only in the archive.

Highest-value archive-only items:
1. **Google Drive OAuth credentials directory** (operational — needed for Airbnb Drive path).
2. **`airbnb_scraper/outreach/`** — Sheets-era owner outreach (LINE + wa.me drafts; parallel to Agent 7).
3. **`Монтаж локал`** — DepthFlow local reel alternative (experiment).
4. **`agent_4_publisher_fb` kit** — packaged FB Chromium profile + Notion/R2 notes.
5. **Reel audio asset folder** + **HANDOFF_PRICES_CHAT.md** ops docs / nested VPS logs.

Highest-value refactor-only improvements (do **not** revert):
- Wazzup WhatsApp client runtime, allowlist, ownership/handoff transport
- `contact_role` + WhatsApp UI list sync
- Agents 9 / 10
- PostMyPost as primary social publisher
- Canonical `agent6_qualifier` / `agent7_envoy` / `agent8_notary` + much larger tests

---

# Archive Project Identified

## Candidates

| Path | Why considered | Verdict |
|------|----------------|---------|
| `/Users/lifefmg/Desktop/Агенты/Real Estate Agent` | Same agent tree (`agent_1`…`agent_8`), shared schema/config, continuous git history, sibling of refactor | **PRIMARY ARCHIVE** |
| `/Users/lifefmg/Desktop/Агенты/ai_real_estate_backups/2026-08-05/` | Contains `ai_real_estate-main-github-zipball.zip` only | Secondary snapshot, not working tree |
| `/Users/lifefmg/Desktop/Real_Estate_Agents_SAFE_AUDIT.zip` | Zip audit artifact | Not full project |
| `/Users/lifefmg/Desktop/real-estate-clawbot-20260702.zip` | Older clawbot zip | Not primary |
| `/Users/lifefmg/Desktop/Real estate` | Different/older desktop folder | Not selected |

**Why primary:** Identical multi-agent layout, live `.env`/sessions, nested production Airbnb bot (`Agent-real-estate-1`), and is explicitly the non-`- refactor` counterpart.

**Git heads (info only):**
- Archive: `c8c5d96` 2026-08-05 — PostMyPost publisher switch (~79 commits)
- Refactor: `2e1e3c1` 2026-08-08 — amoCRM MCP (+ more WA/role work after)

---

# Architecture Comparison

| Area | Archive | Refactor |
|------|---------|----------|
| Top agents 1–8 | Present | Present (+ ROLE_MAP) |
| Agent 9 / 10 | Absent | Present (`Агент 9`, `Агент 10`) |
| WhatsApp transport | Spec/planned (Wazzup/Green-API); no Wazzup runtime | Full Wazzup stack |
| `contact_role` / `whatsapp_ui_sync` | Absent | Present |
| `amoCRM MCP` | Absent | Present |
| `agent_4_publisher_fb` | Present (kit + profile) | **Missing** |
| `Монтаж локал` | Present (DepthFlow experiment) | **Missing** |
| Reel audio folder | Present | **Missing** (as top-level folder) |
| Airbnb folder name | `airbnb_scraper` (+ nested duplicates) | `airbnb_parser` (cleaned, more pricing modules) |
| Agent 6 package layout | Monolith `agent7.*` + `agent8.*` | Canonical `agent6_qualifier` + shims |
| Publisher primary | README: Metricool + ChatPlace + phone; code already moving PostMyPost | PostMyPost primary; Metricool/ChatPlace disabled |
| Docs | README + HANDOFF_PRICES | README + ROLE_MAP + FULL_LOCAL_E2E_READINESS + Wazzup docs |

---

# Agent-by-Agent Comparison

## Agent 1 — Parser

| | Archive | Refactor |
|--|---------|----------|
| FB Marketplace | `fb_parser` working | PRESERVED / IMPROVED |
| Airbnb | `airbnb_scraper` + nested `Agent-real-estate(-1)` | Renamed `airbnb_parser`; pricing modules expanded |
| Drive credentials | `credentials/` **present** | **MISSING directory** |
| Sheets / Supabase / TG bot | Present | Present |
| Owner outreach subpackage | `outreach/` (Sheets + Claude + LINE/WA links) | **MISSING** |
| Price workers | In nested `Agent-real-estate-1/scripts` | Top-level `scripts/price_worker_*.py` |

**Verdict:** **PARTIALLY MIGRATED** — core scrape improved; ops credentials + legacy `outreach/` not in refactor tree.

## Agent 2 — Registrar

Both have `_import/assistant-media` chain docs/code. Archive tree is much larger on disk (venvs/media). Logic appears **PRESERVED**.

**Verdict:** **PRESERVED** (refactor cleaner; verify media assets case-by-case when needed).

## Agent 3 — Director

Video/Seedance/Higgsfield present both sides. Archive also has separate **`Монтаж локал`** DepthFlow path.

**Verdict:** **PRESERVED** core; **MISSING** experimental local montage package.

## Agent 4 — Publisher

| | Archive | Refactor |
|--|---------|----------|
| PostMyPost | Present (late) | Primary / IMPROVED |
| Metricool / ChatPlace | Still prominent in README; scripts remain | Disabled in config; scripts remain |
| Phone social | `agent_4_publisher_social` | PRESERVED / expanded |
| FB kit package | `agent_4_publisher_fb` | **MISSING** |

**Verdict:** **IMPROVED** for API publish; **PARTIALLY MIGRATED** for FB browser kit packaging.

## Agent 5 — Usher

Archive: README-only stub (ChatPlace-oriented).  
Refactor: PostMyPost AI agent adapter + social inbound contract (still pending network trigger).

**Verdict:** **IMPROVED** (still incomplete network-wise) — ChatPlace path **INTENTIONALLY REMOVED**.

## Agent 6 — Qualifier

Archive: TG userbot + Gemini + amo; WhatsApp as **field/CRM**, not inbound transport.  
Refactor: same business core + Wazzup shared core + more tests/services (amo tasks, publication resolver, etc.).

Templates: `config/templates.json` identical (comment-only); real strings in `templates.py` — preserved via canonical package.

**Verdict:** **IMPROVED** (WA transport new); TG flow **PRESERVED**.

## Agent 7 — Envoy

Archive: `agent8/auto.py`, `outreach.py`, calendar, owner_result inside Agent 6 tree.  
Refactor: canonical `agent7_envoy` + WA controlled outreach + gates.

Archive Airbnb `outreach/` is a **separate older Sheets outreach agent**, not the same as Envoy.

**Verdict:** Envoy **IMPROVED**; Sheets/LINE outreach package **MISSING** (evaluate separately).

## Agent 8 — Notary

Both: booking request docx generator + amo attach.  
Archive README claimed “договор аренды”; AGENT_SPEC/refactor clarify booking ≠ lease. Full lease **never implemented** in either.

**Verdict:** **PRESERVED / IMPROVED** tests; full lease still **NOT IMPLEMENTED** (not a regression).

## Agent 9 / 10

Archive: absent. Refactor: present.

**Verdict:** **IMPROVED** (new).

---

# Full E2E Flow Comparison

| Step | Archive | Refactor | Gap? |
|------|---------|----------|------|
| Property input | FB + Airbnb TG/CLI | Same | No |
| Parsing | Yes | Yes (+ pricing modules) | Drive creds ops gap |
| Notion object | Yes | Yes | No |
| Enrichment / prices | Hybrid VPS+Mac workers (documented) | Workers present | Ops doc only in archive HANDOFF |
| Media / video | Seedance + experimental DepthFlow | Seedance | DepthFlow missing |
| Publication | Metricool/ChatPlace/phone → PostMyPost emerging | PostMyPost | Intentional shift |
| Lead inbound | Telegram | Telegram + WhatsApp | Refactor ahead |
| Qualification | Yes | Yes | No |
| Matching | Yes | Yes | No |
| Owner outreach | Envoy TG (+ manual WA/Airbnb/FB); Sheets outreach helper | Envoy TG + controlled WA | Sheets/LINE helper missing |
| Booking doc | Yes | Yes (WA file deferred) | WA media gap is refactor limitation |
| Full lease contract | No | No | None lost |

---

# Property Sources Comparison

| Source | Archive | Refactor | Lost? |
|--------|---------|----------|-------|
| Airbnb URL | SUPPORTED | SUPPORTED | No (rename) |
| Facebook Marketplace URL | SUPPORTED | SUPPORTED | No |
| Facebook Page as ingest | NO | NO | — |
| Telegram as client channel | SUPPORTED | SUPPORTED | No |
| Manual / Notion | PARTIAL | PARTIAL | No |
| Google Drive as photo store | SUPPORTED (creds in archive) | PARTIAL (code yes, creds missing) | **Ops gap** |
| Google Sheets CRM | SUPPORTED | SUPPORTED | No |
| Other listing sites | NO | NO | — |

---

# Airbnb

| Capability | Archive | Refactor |
|------------|---------|----------|
| Source URL input | YES | YES |
| Scraper / ingestion | YES | YES (expanded pricing) |
| Owner contact extraction | YES (`owner_detect` in nested/refactor) | YES |
| Sheets outreach drafts | YES (`outreach/`) | **NO package** |
| LINE deep links in outreach | YES | **NO** |
| Publishing to Airbnb | NO | NO |
| Official Airbnb API | NO | NO |
| Browser session | YES | YES |
| Drive OAuth files | YES on disk | **NO** |
| Nested VPS bot copies / logs | YES (`Agent-real-estate-1`) | Cleaned (good) |

**Answer:** There **was** working Airbnb ingestion in archive; refactor **still has** it (often improved). What is missing is mainly **Drive credentials**, **legacy Sheets/LINE outreach package**, and nested ops history — **not** the core scraper.

---

# Facebook

| Contour | Archive | Refactor | Note |
|---------|---------|----------|------|
| Marketplace ingestion | YES | YES | Preserved |
| Page publish (PostMyPost) | Emerging | Primary | Improved |
| Groups / MP publish (phone) | YES | YES | Preserved |
| FB publisher kit + profile | YES | Missing folder | Profile may still exist under fb_parser elsewhere — kit packaging lost |
| Agent 9 Messenger owner | NO | YES | New |
| FB messaging in Envoy | Manual/alert | Manual/alert + Agent9 | Improved option |

---

# Telegram

| Item | Archive | Refactor |
|------|---------|----------|
| Userbot sessions | Present | Present (+ official_company sessions) |
| Qualifier handlers | Monolith `agent7.tg_userbot` | Split + shims |
| Owner reply routing | Present | Present |
| humanized_respond | Present | Present |
| Folders / role folders | Present | Present |
| Useful guards lost? | Not evident | More ownership/WA guards added |

**Verdict:** **PRESERVED / IMPROVED** — no critical TG handler loss found.

---

# WhatsApp

| Item | Archive | Refactor |
|------|---------|----------|
| Client field capture | YES | YES |
| Owner WA Notion field | YES | YES |
| Wazzup webhook/send runtime | **NO** (spec only; example `WA_PROVIDER=greenapi`) | **YES** |
| Green-API working code | **NO** (placeholder) | Legacy placeholders unused |
| WA Web list mirror | NO | YES (`whatsapp_ui_sync`) |
| wa.me draft links (Airbnb outreach) | YES in `outreach/` | Missing with that package |

Archive did **not** have a working Wazzup Agent 6 inbound loop. Refactor added it.

---

# amoCRM

Both implement contact/lead create, pipeline «Аренда — лиды» stages, WhatsApp custom field, dedup patterns.  
Refactor adds: contact role dual sync, amo tasks service, MCP read server.

**No evidence of a lost critical amo automation unique to archive.**  
**Verdict:** **PRESERVED / IMPROVED**.

---

# Notion

Shared `schema/notion_schema.json` contract approach both sides. Publisher field naming still uses legacy `agent6_*` in both ecosystems.  
Refactor AGENT_SPEC / ROLE_MAP clearer on WA writebacks.

**No major useful field mapping found only in archive and dropped.**  
**Ops gap:** Drive gallery URLs depend on successful Agent 1/2 media path (credentials).

---

# Publication / PostMyPost

Archive late commit already switched toward PostMyPost; README lagged.  
Refactor: fuller PostMyPost config, reply agent, UTM, deferred sync, disabled Metricool/ChatPlace.

**Lost:** packaged `agent_4_publisher_fb` profile kit (not PostMyPost).  
**Not lost:** phone social publisher.

---

# Content / Media

| Item | Archive | Refactor | Class |
|------|---------|----------|-------|
| Seedance / Higgsfield | YES | YES | WORKING |
| R2 media | YES | YES | WORKING |
| DepthFlow `Монтаж локал` | YES | NO | EXPERIMENT / WORKING CODE in archive |
| Reel audio MP3s | YES | NO top-level | ASSETS |
| Canva | NO | NO | PLANNED ONLY (none) |
| ElevenLabs | NO | NO | — |
| Pinterest | mentions only | mentions only | NOT a pipeline |

---

# Prompts / Templates

| Location | Finding |
|----------|---------|
| `agent_6_qualifier/config/templates.json` | Identical (comment stub) |
| `templates.py` (Agent 6/7) | Migrated to canonical package; core strings preserved |
| Airbnb `outreach/agent.py` Claude compose prompts | **Archive-only** — Sheets outreach drafts |
| Agent 9 Gemini prompts | Refactor-only |
| Agent 10 LLM prompts | Refactor-only |

**No evidence Agent 6 qualification templates were shortened away.** Refactor AGENT_SPEC is longer and more precise.

---

# Env / Credentials

`.env.example` key sets: archive ~124, refactor ~210.

| Only in archive examples | Relevant? |
|--------------------------|-----------|
| `GITHUB_TOKEN` | NO for E2E (tooling) |

Refactor added WAZZUP_*, CONTACT_ROLE_*, META_*, AGENT9/10_*, MCP_*, etc.

**Operational (not example) gap:** archive `airbnb_scraper/credentials/` contains Google OAuth/service-account files; refactor has **no** `credentials/` dir under `airbnb_parser`.

**Still relevant:** YES for Airbnb Drive — **INVESTIGATE** manual secure copy by user (do not automate in this audit).

---

# Persistent State

| Store | Archive | Refactor |
|-------|---------|----------|
| TG sessions | YES | YES |
| Agent 6 session JSON | YES | YES (+ WA sessions) |
| owners.json | YES | YES |
| Airbnb price worker state | Nested under Agent-real-estate-1 | pricing_queue / cache under airbnb_parser/data |
| FB publisher kit profile | YES | Missing kit path |
| FB parser storage_state | YES (typical) | YES |
| Drive tokens | In archive credentials | Missing |
| Outreach thread store | `outreach/threads.py` JSON | Missing with package |

---

# Workers / Queues

| Worker | Archive | Refactor |
|--------|---------|----------|
| Airbnb TG bot | YES | YES |
| price_worker_local + keepalive | YES (nested) | YES (top-level scripts) |
| Agent 4 publisher / deferred sync | YES | YES |
| Phone social jobs | YES | YES |
| Wazzup webhook server | NO | YES |
| WA UI queue worker | NO | YES |
| Agent 9 Notion poller | NO | YES |
| Sheets outreach agent | YES (`outreach`) | NO |

---

# Error Handling / Idempotency

Refactor adds stronger WA idempotency (processed sqlite, crmMessageId, allowlist, stage mode).  
Archive had FB session locks, price Access Denied diagnostics, amo dedup tests.

**No critical safeguard uniquely lost**; WA side is stronger in refactor.

---

# Human Handoff

Archive: `handoff_to_human` in Qualifier + wait template.  
Refactor: same + WA ownership `HUMAN_HANDOFF` / bot outbound ignore.

**Verdict:** **IMPROVED**.

---

# Booking / Notary / Contract

| Feature | Archive | Refactor |
|---------|---------|----------|
| Booking request docx | YES | YES |
| amo attach | YES | YES |
| Full rental contract | NO (README aspirational) | NO (explicitly documented) |
| Payment/deposit automation | NO | NO |

**Nothing valuable lost** on contracts; naming in archive README was ahead of code.

---

# Tests

| | Archive Agent6 | Refactor Agent6 |
|--|----------------|-----------------|
| test_*.py count | 8 | 22 |

Refactor covers Wazzup, owner handler service, notary guards, publication resolver, WA parity, etc.  
Archive tests for core qualifier/envoy remain conceptually covered.

---

# Docs / Startup Commands

Archive-only high value:
- `HANDOFF_PRICES_CHAT.md` — VPS vs Mac price collection hybrid, Access Denied notes, worker commands
- `Монтаж локал/README.md` — DepthFlow startup
- `agent_4_publisher_fb/.../README.md` — FB profile caveats (Keychain-bound)

Refactor-only: `ROLE_MAP.md`, `README_WAZZUP.md`, readiness audits, Agent 9/10 docs.

---

# Missing in Refactor

1. `agent_1_parser/airbnb_parser/credentials/` (Drive OAuth artifacts)  
2. `agent_1_parser/.../outreach/` package (Sheets/LINE/wa.me draft agent)  
3. Nested `airbnb_scraper/Agent-real-estate*` ops copies/logs (intentionally cleaned)  
4. Top-level `Монтаж локал/`  
5. Top-level reel audio folder  
6. `agent_4_publisher_fb/` kit  
7. `HANDOFF_PRICES_CHAT.md`  
8. Archive README Metricool/ChatPlace “primary” story (intentionally obsolete)

---

# Improved in Refactor

- Wazzup WhatsApp client E2E path  
- Contact role + WA UI sync  
- Agents 9 & 10  
- PostMyPost-first publishing  
- Agent 6/7/8 package clarity + tests  
- Airbnb pricing modules at clean paths  
- Stronger live safety flags / allowlists  
- amoCRM MCP (read tooling)

---

# Legacy Features Not Worth Migrating

| Item | Why ignore / keep archive-only |
|------|--------------------------------|
| Nested `Agent-real-estate` / `Agent-real-estate-1` duplicates | Messy double trees; logic migrated |
| Green-API placeholder as primary | Never implemented; Wazzup chosen |
| ChatPlace as Agent 5 core | Explicitly removed |
| Metricool as primary publisher | Disabled on purpose |
| DepthFlow montage (unless product wants offline video) | Experiment; Seedance is production path |
| Sheets outreach agent wholesale | Overlaps Agent 7; only cherry-pick LINE/wa.me UX if needed |
| GITHUB_TOKEN example | Unrelated to E2E |
| Huge venv/media blobs in archive trees | Not “features” |

---

# P0 Critical Gaps

1. **Google Drive credentials absent in refactor** → Airbnb photo Drive path blocked operationally.  
   **Recommendation:** INVESTIGATE — user-controlled secure restore from archive `credentials/` (do not commit).  
2. *(Conditional)* If full E2E requires FB Groups/MP browser kit profile from `agent_4_publisher_fb` and current profiles fail → restore kit/profile carefully.  
   **Recommendation:** INVESTIGATE only if phone FB publish is in minimum path.

---

# P1 Important Gaps

1. Archive **HANDOFF_PRICES_CHAT.md** operational knowledge (VPS collect off / Mac worker).  
   **MIGRATE** as doc into refactor (copy content later — not done now).  
2. **`outreach/` LINE + wa.me draft UX** if managers still use Sheets-assisted owner messaging.  
   **REIMPLEMENT CLEANLY** into Agent 7 tooling if still needed — don’t paste Sheets agent as-is.  
3. **Reel audio assets** if montage/reels still use that folder.  
   **MIGRATE assets** when video E2E needs them.

---

# P2 Useful Gaps

1. `Монтаж локал` DepthFlow offline alternative.  
2. Nested price_worker logs/state as historical debug.  
3. Archive FB kit README Keychain warnings (document in refactor).

---

# Recommended Migration Plan

| Priority | Action |
|----------|--------|
| P0 | Manually verify/restore Drive credentials into refactor Airbnb path (user). |
| P0/P1 | Confirm whether `agent_4_publisher_fb` profile is still required vs current fb_parser/social profiles. |
| P1 | Port `HANDOFF_PRICES_CHAT.md` insights into refactor docs. |
| P1 | Decide if LINE/wa.me draft helper from `outreach/` should be a small Agent7 utility. |
| P2 | Optionally vendor DepthFlow montage or keep archive as reference. |
| — | Do **not** reintroduce ChatPlace/Metricool/Green-API as primary. |
| — | Do **not** flatten Agents 9/10 / Wazzup / contact_role away. |

---

# Full E2E Impact

| Question | Impact |
|----------|--------|
| Can core E2E run without archive? | **Mostly yes** if Drive not required and FB phone kit not required |
| Airbnb ingest without archive? | **Yes** (code in refactor) |
| Airbnb Drive galleries? | **Blocked** until credentials restored |
| WA client E2E? | **Better in refactor** (archive lacked Wazzup runtime) |
| Owner outreach? | **Better TG/WA gates in refactor**; archive Sheets/LINE helper optional |
| Booking doc? | **Same capability** |
| Publication? | **Better PostMyPost in refactor**; FB kit packaging is the main archive leftover |

---

# Final Verdict

**Refactor is incomplete vs “all archive folders/assets”, but not incomplete vs core rental E2E architecture.**  
Most archive-only code is either **superseded**, **experimental**, or **operational credentials/docs**.  

**Do not mass-migrate.** Cherry-pick: **Drive credentials (ops)**, **price handoff docs**, optionally **LINE/wa.me helper ideas**, **audio/FB kit if those channels are in the live plan**.

---

## Critical gap answers (checklist §26)

1. **Airbnb working flow missing?** No for scrape; **yes** for Drive creds + Sheets/LINE outreach package.  
2. **Property source/parser missing?** No.  
3. **Publication channel missing?** Metricool/ChatPlace intentionally dropped; **FB kit packaging** missing.  
4. **Agent6 templates lost?** No evidence.  
5. **Agent7 flow lost?** No; archive Sheets outreach is separate.  
6. **Agent8 booking lost?** No; full lease never existed.  
7. **amoCRM automation lost?** No; refactor adds more.  
8. **Notion mapping lost?** No major.  
9. **State stores/queues lost?** Drive tokens + outreach threads + FB kit profile.  
10. **Useful startup scripts?** price workers present; handoff doc / montage / FB kit READMEs archive-only.  
11. **Auth/session instructions needed?** Yes — Drive OAuth, FB kit Keychain notes, price hybrid ops.  
12. **Refactor TODO already done in archive?** Wazzup was TODO in archive and **implemented in refactor**. Full lease TODO in both. ChatPlace “done” in archive docs but superseded.
