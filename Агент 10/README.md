# Agent 10 — Meta Ads Performance Marketer

Isolated module: pick the best **Instagram / Facebook** creative for an object and promote it via **Meta Ads only**. **No real ad spend in V1.**

> Official scope lock: [`SCOPE.md`](SCOPE.md)  
> **SOURCE CONTENT:** Instagram + Facebook · **PAID ADS:** Meta Ads only · **OTHER SOCIAL ADS:** out of scope

## Role

Agent 10 answers:

1. Which Instagram Reel (primary) — or other IG/FB creative — for object `X` performs best organically?
2. What Meta Ads objective / budget / duration / strategy (within hard caps)?
3. Is the recommendation approved?
4. (Future) Did **Meta** paid promotion produce qualified leads / bookings?

It does **not** publish content, run non-Meta ads, talk to clients, or touch Agents 6–9 runtime.

## Architecture

```
Object ID
  → Publication Ledger / Notion (IG + FB only for Agent 10)
  → PostMyPost analytics [stub/mock in V1]
  → Creative analysis (V1 scorer = Instagram Reels)
  → recommendation + Meta budget caps
  → human approval
  → Meta Ads adapter [DISABLED in V1]
```

Future:

```
Object ID → Ledger (instagram|facebook)
  → analytics → best candidate → approval
  → Meta Marketing API (Campaign → Ad Set → Creative/existing post → Ad)
  → Meta Insights → Business Score
  → Agent 6 / amoCRM outcomes → Meta optimization → content feedback
```

**Isolation:** no Python imports from Agents 1–9. Runtime state only under `Агент 10/data/`.

## Publication Ledger (Agent 4 owned)

Agent 10 is a **read-only** consumer of:

`agent_4_publisher/data/publications.sqlite3`

via `PublisherLedgerReader` (`platform IN instagram|facebook` only).

Without PostMyPost analytics, CLI shows publication history and blocks organic ranking — no fake metrics.


## Current V1

| Capability | Status |
|---|---|
| Notion object lookup (read-only) | Adapter ready (live if credentials set) |
| Map existing `post_url_instagram_reel` etc. | Yes |
| Multiple Reels per object | Via PostMyPost adapter / fixtures (Notion stores **one** reel URL per object today) |
| PostMyPost analytics | **Stub/Mock only** — no invented endpoints |
| Organic Score + ranking | Deterministic, configurable weights |
| Budget recommendation with caps | Yes |
| Approval → READY_TO_LAUNCH | Yes |
| Meta campaign create/spend | Adapter ready; **writes off**; ACTIVE blocked |
| Business Score / paid metrics | Normalization from Meta Insights + model |
| Agent 6 / amoCRM events | Schema only |
| CreativeInsight | Schema only |
| MarketingBrain / LLM | Interface only (disabled); never picks winner / never holds Meta token |

## Future V2 / V3 / V4

- **V2:** Confirm PostMyPost analytics API contract; wire real `get_post_analytics` / history windows (24h / 72h / 7d) without fabricating series.
- **V3:** Meta Marketing API — boost existing Reel / create campaign from approved READY_TO_LAUNCH; paid insights → Business Score.
- **V4:** Consume Agent 6 / amoCRM events (`lead_created`, `lead_qualified`, `lead_disqualified`, `booking_confirmed`); CreativeInsight feedback loops.

## Flow

```
Object ID
→ Publication Ledger (filter: instagram | facebook)
→ PostMyPost analytics
→ CreativeScorer (primary: Instagram Reels)
→ recommendation (Meta objective / budget / strategy)
→ approval
→ Meta Ads   ← blocked until Meta Marketing API enabled
```

TikTok / other Publisher networks may appear in a shared ledger for Agent 4 — Agent 10 ignores them.

## Notion audit (read-only; schema NOT modified)

Source of truth: `schema/notion_schema.json`.

| Need | Actual field | Present? |
|---|---|---|
| Object ID | `Объект ID` (rich_text) | Yes |
| Instagram Reel URL | `post_url_instagram_reel` (url) | Yes |
| Instagram Carousel URL | `post_url_instagram_carousel` (url) | Yes |
| Facebook URL | `post_url_facebook` (url) | Yes |
| Publication datetime | `Дата и время публикации` (date) | Yes |
| Metricool IDs | `metricool_post_id`, `metricool_post_group_id` | Yes |
| Peer: property type | `Тип жилья` | Yes |
| Peer: district | `Район` | Yes |
| Peer: rent type | `Тип аренды` | Yes |
| Peer: bedrooms | `Количество комнат` | Yes |
| Peer: price | `Цена за месяц` → derived `price_band` | Yes |
| PostMyPost post ID | — | **No** |
| Instagram Media ID | — | **No** |
| Facebook Post ID | — | **No** |
| Format column | — | **No** (inferred from field name) |
| Analytics fields | — | **No** |

### Proposal (do not auto-apply)

Preferred future publication record (separate DB or Notion child DB):

- `object_id`, `platform`, `format`
- `postmypost_post_id`, `instagram_media_id`, `instagram_permalink`, `facebook_post_id`
- `published_at`, `status`

Optional analytics columns or Agent 10-local snapshots (preferred): keep analytics in `Агент 10/data/` SQLite.

## PostMyPost integration status

| Item | Status |
|---|---|
| Publishing credentials in Agent 4 | `POSTMYPOST_API_TOKEN` / `POSTMYPOST_TOKEN`, base `https://api.postmypost.io/v4.1` |
| Documented analytics endpoints in this repo | **Not found** |
| Agent 10 adapter | Interface + `StubPostMyPostAdapter` / `MockPostMyPostAdapter` |
| Live analytics | **Not wired** (no fake data) |

Logical contract:

- `get_publications(object_id)`
- `get_post_analytics(post_id)` → dict with optional metrics (omit missing; never coerce to 0)
- `get_account_analytics(...)`

## Analytics contract

Canonical metrics (`None` if unavailable — never `0` for missing):

`publication_id`, `object_id`, `platform`, `format`, `published_at`, `age_hours`,
`reach`, `impressions`, `views`, `likes`, `comments`, `saves`, `shares`

Derived (None if inputs missing / zero denominator):

`save_rate`, `comment_rate`, `share_rate`, `engagement_rate`, `reach_velocity`, `view_velocity`

Windows config: `24h` / `72h` / `7d`. Time-series history is **not simulated** when unavailable; V1 uses current metrics + age normalization (`metric / max(age_hours, min_age)`).

## Organic Score formula

Configurable weights in `config/scoring_weights.json` (not hardcoded in business logic).

Default components: `save_rate`, `comment_rate`, `share_rate`, `engagement_rate`, `reach_velocity`, `view_velocity`.

1. Compute available component values.
2. Drop missing components (missing ≠ poor).
3. Renormalize remaining weights to sum=1.
4. Relative 0..1 scale within peer group (same object Reels in V1).
5. `organic_score = Σ(rel_i × 100 × w_i)` clamped to `[0, 100]`.

Winner is always chosen by this scorer — never by LLM.

## Approval state machine

```
DRAFT → ANALYZED → PROPOSED → APPROVED → READY_TO_LAUNCH → LAUNCHED → PAUSED → COMPLETED
```

V1 ceiling: **READY_TO_LAUNCH** while Meta is disabled. Launch without approval raises. Meta disabled raises `MetaIntegrationDisabled`.

Stored locally: `object_id`, `publication_id`, creative score, budget, duration, `approved_by`, `approved_at`.

## Meta adapter status

**Only** Meta Ads:

- `MetaAdsAdapter` (interface)
- `DisabledMetaAdsAdapter` / `MockMetaAdsAdapter`
- `MetaMarketingApiAdapter` — real Graph / Marketing API (HTTP)

No multi-network ads abstraction. No browser / Graph Explorer / MCP as production transport.

### Current Meta stage

| Item | Status |
|---|---|
| Marketing API connected | Yes (`MetaMarketingApiAdapter`) |
| Read-only diagnostics | `scripts/meta_diagnose.py` (GET only) |
| Writes | **Disabled by default** (`META_WRITE_ENABLED=false`) |
| ACTIVE ads | **Blocked** (`META_ACTIVE_ENABLED=false`) |
| PAUSED create contract | Implemented + budget/approval guards |
| MarketingBrain / LLM → Meta | **Not connected** |
| Live spend in tests | Never |

### Safety

```
META_WRITE_ENABLED=false   → read-only GETs
META_WRITE_ENABLED=true
META_ACTIVE_ENABLED=false  → PAUSED creates only (still requires approval at service layer)
META_ACTIVE_ENABLED=true   → NOT ready for production automatic launch
```

**Housing note:** the technical smoke campaign uses `special_ad_category=NONE`.  
Production real-estate campaigns must be created separately with Meta `HOUSING` special ad category and housing-compliant targeting. Do not promote the smoke campaign to production housing delivery.

### Credentials (local `.env` only)

```
META_APP_ID=1051487844031310
META_BUSINESS_ID=1572755037543832
META_AD_ACCOUNT_ID=3462495317264561
META_PAGE_ID=1189108177625326
META_INSTAGRAM_ACCOUNT_ID=17841410402639080
META_ACCESS_TOKEN=          # you fill locally — never commit
META_GRAPH_API_VERSION=v26.0
META_API_BASE_URL=https://graph.facebook.com
META_EXPECTED_ACCOUNT_NAME=Open Home th
META_EXPECTED_CURRENCY=THB
META_EXPECTED_TIMEZONE=Asia/Bangkok
```

`META_APP_SECRET` is optional and not required for current server-to-server calls.

### Read-only diagnostic

```bash
cd "Агент 10"
cp .env.example .env   # then set META_ACCESS_TOKEN locally
PYTHONPATH=src python3 scripts/meta_diagnose.py
```

Never prints the token. GET only.

### Future

Publication analytics → Organic Score → MarketingBrain → policy → approval → Meta (PAUSED) → Insights → Business Score → Agent 6 / amoCRM feedback → controlled optimization.

Advantage+ handles delivery/audience/placements; Agent 10 owns strategy, creative choice, budget caps, timing, pause/restart.
## Business Score (future — Meta Insights only)

`PaidPerformanceMetrics`: spend, impressions, reach, cpm, clicks, ctr, cpc, leads, cpl, qualified_leads, cost_per_qualified_lead, bookings, cost_per_booking — all optional.

`BusinessScorer.score(...)` interface — not implemented in V1; no fake fills.

## Agent 6 / amoCRM (future contract only)

Events (schema in `models.py`): `lead_created`, `lead_qualified`, `lead_disqualified`, `booking_confirmed`.

Agent 6 and amoCRM are **not** modified in V1.

## Setup

```bash
cd "Агент 10"
cp .env.example .env
pip install -r requirements.txt
PYTHONPATH=src python3 scripts/analyze_object.py 1847 --fixture --report
PYTHONPATH=src python3 scripts/recommend_ad.py 1847 --fixture
PYTHONPATH=src pytest tests/ -q
```

## CLI output (example)

```
Object: 1847
Reels found: 4
...
Recommended:
Reel ...
Reason:
- save_rate: ...
Suggested Ads:
Objective: ...
Budget/day: 500 THB
Duration: 5 days
Maximum spend: 2500 THB
Meta launch:
DISABLED
```
