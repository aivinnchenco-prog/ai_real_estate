# Agent 10 — Official Scope Lock

**Agent 10 = Meta Ads / Facebook Ads Performance Marketer only.**

Not a generic social-ads agent. Not a multi-network ads router.

## Paid advertising platform

**ONLY: Meta Ads** (Meta / Facebook Ads Manager / Marketing API).

Out of scope forever for Agent 10:

- Google Ads
- TikTok Ads
- YouTube Ads
- LinkedIn Ads
- Telegram Ads
- Pinterest Ads
- X Ads
- any other paid network

Do **not** create `TikTokAdsAdapter`, `GoogleAdsAdapter`, `SocialAdsAdapter`, or `MultiPlatformAdsRouter`.

Adapters allowed:

- `MetaAdsAdapter` (interface)
- `DisabledMetaAdsAdapter` / `MockMetaAdsAdapter` (offline / default)
- `MetaMarketingApiAdapter` (real Graph / Marketing API over HTTP)

## Meta stage (current)

- Marketing API client connected (HTTP → `graph.facebook.com`)
- Read-only diagnostics (`scripts/meta_diagnose.py`)
- Writes disabled by default: `META_WRITE_ENABLED=false`
- ACTIVE launch blocked: `META_ACTIVE_ENABLED=false`
- Create path (when write enabled later) forces `PAUSED` only
- LLM / MarketingBrain not wired to Meta spend actions

## Safety switches

| Env | Default | Effect |
|---|---|---|
| `META_WRITE_ENABLED` | false | Blocks all POST/PATCH/DELETE before network |
| `META_ACTIVE_ENABLED` | false | Rejects ACTIVE status / resume |
| `META_ADS_ENABLED` | false | Service does not auto-wire live adapter unless token set |

## Future

- Publication analytics + Organic Score
- MarketingBrain recommendations (no direct Meta token)
- Approval → PAUSED creation
- Meta Insights → Business Score
- Agent 6 / amoCRM feedback
- Controlled optimization (pause/restart within caps)

## Creative from existing posts

- Facebook existing post: `create_creative_from_existing_facebook_post` (object_story_id)
- Instagram existing media: interface + TODO until official Graph contract confirmed for current API version — no live POST guessing


## Source publications (creative content)

Agent 10 analyzes **published** content from:

| Allowed | Notes |
|---|---|
| Instagram Reel | **Primary V1 creative** for CreativeScorer |
| Instagram Carousel | Allowed source; separate scoring path if used |
| Instagram Post | Allowed source |
| Facebook Post | Allowed source; separate from Reel scorer |
| Facebook Reel | Only if format is reliably detectable from PostMyPost / ledger contract |

**Ignored:** TikTok, YouTube, LinkedIn, Threads, X, Telegram, and any other platform rows in a shared Publication Ledger.

Query scope:

```text
platform IN ("instagram", "facebook")
```

## Creative source ≠ ads platform

- Creative/source post may live on **Instagram** or **Facebook**.
- Paid campaign is **always** created via **Meta Ads** (Marketing API / Ads Manager).
- An Instagram Reel can be the winning creative and still be promoted only through Meta.

## Scoring policy

- Do not force one score formula across incompatible formats (e.g. IG Reel vs FB static post).
- V1 primary `CreativeScorer` = **Instagram Reels**.
- Facebook creatives = separate source type / scorer path.

## Paid performance (Meta Insights only)

Future Business Score / optimization uses Meta metrics only, e.g.:

spend, impressions, reach, CPM, clicks, CTR, CPC, leads, CPL,
qualified_leads, cost_per_qualified_lead, bookings, cost_per_booking

No performance contracts for other ad networks.

## Closed loop (future)

```
Instagram/Facebook content
  → Agent 10
  → Meta Ads
  → lead
  → Agent 6
  → amoCRM
  → qualified / disqualified / booking
  → Agent 10 Business Score
  → Meta optimization
  → content feedback
```

## Publication Ledger

Shared ledger may still store TikTok/other rows for Agent 4 Publisher compatibility.

Agent 10 **must ignore** those rows. Ledger work continues; this scope does not expand Agent 10 to other social ads.
