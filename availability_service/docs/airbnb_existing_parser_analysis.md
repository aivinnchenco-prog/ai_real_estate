# Airbnb monthly pricing / availability — existing code analysis

**Date:** 2026-08-14  
**Scope:** Read-only analysis for Availability Service V1 → Airbnb provider planning.  
**No live scraping, no code changes to Agent1/Agent2 runtime.**

## Executive summary

| Question | Answer |
|----------|--------|
| Where is Airbnb scraping? | **Agent1** (`agent_1_parser/airbnb_scraper/Agent-real-estate-1/`), not Agent2 |
| What does Agent2 do? | Consumes `parsed.json` → writes Notion (`monthly_prices`, `Цена за месяц`) |
| Background month enrichment | Agent1 thread during handoff **or** `scripts/price_worker_local.py` on Mac |
| Browser for prices | SeleniumBase/CDP Chrome (`AirbnbParser`) |
| Browser for calendar | Playwright Chromium (`availability.fetch_calendar_days`) |
| Availability status enum? | **No** — per-day `available` in calendar + price entry `status` |
| Default months collected | Agent2 `pipeline.json`: **3**; worker can use up to 12 via env |

## Architecture

```
Agent1 take_listing_to_work(url)
  → AirbnbParser.process_url()          # listing SSR + first-month price seed
  → _agent2_path()
      → threading: _collect_prices()     # calendar + monthly_pricing (optional)
      → handoff_to_agent2()             # parsed.json with monthly_prices
  → agent2_structurize.py
      → apply_parsed_meta()               # Notion monthly_prices multi_select

Deferred (VPS PRICE_COLLECT_ENABLED=0):
  → seed 1 month only, prices_deferred=true
  → Mac: price_worker_local.py polls Notion → collect_for_url → PATCH Notion
```

Agent2 `AGENTS.md` explicitly forbids browser scraping for listings.

## Key files

### Agent1 — scraping & pricing core

| File | Role |
|------|------|
| `workflow.py` | `_collect_prices`, `_agent2_path`, `_seed_from_listing_price` |
| `monthly_pricing.py` | Pure logic: `price_for_month`, `collect_monthly_prices`, parallel variant |
| `availability.py` | Playwright → intercept `PdpAvailabilityCalendar` → `{date: bool}` |
| `airbnb_parser.py` | Selenium listing parse + `fetch_price_for_period(check_in, check_out)` |
| `agent2_handoff.py` | Session `parsed.json`, `prices_deferred` flag |
| `scripts/price_worker_local.py` | Background Notion poller for deferred price fill |
| `price_cache.py` | URL-keyed cache for monthly price dicts |
| `config.py` | `PRICE_*` env knobs |

### Agent2 — consumption only

| File | Role |
|------|------|
| `scripts/agent2_structurize.py` | `apply_parsed_meta`, `monthly_price_options`, `first_upcoming_month_entry` |
| `config/pipeline.json` | `price_months_ahead: 3`, Notion field map |

### Related (Qualifier)

| File | Role |
|------|------|
| `agent_6_qualifier/src/agent7/airbnb_check.py` | Same calendar intercept for client date checks |

## Data formats

### `monthly_prices` entry (per month key `YYYY-MM`)

```json
{
  "price": 55000,
  "status": "monthly" | "prorated" | "insufficient_data",
  "period_used": "2026-09-01/2026-09-30",
  "based_on_days": 14,
  "note": "...",
  "source": "listing_parse" | "notion_chip"
}
```

### Calendar

`dict[date, bool]` — `True` = day available, missing day = treated as unavailable in pricing logic.

## Request budget (code analysis, not measured)

Per object (full collect, `months_ahead=N`):

1. **1×** Playwright page load → calendar (~12 months of days in one API response)
2. **Up to N×** Selenium page loads for price (level 1: full month `check_in`/`check_out`)
3. **Up to N×** additional loads if level 2 prorated segment needed (partial month)
4. **Refill pass** may repeat failed months (+ `PRICE_BATCH_PAUSE_SEC` default 8s)
5. **Parallel**: `PRICE_PARALLEL_WORKERS` (default 3) — separate browser per worker

For N=12: roughly **13–37** browser navigations (1 calendar + 12–24 price loads + listing parse is separate).

Stay length: full month uses 1st→last day; segment uses inclusive day count; monthly rate display adjusted for short stays (<27 days).

## Mapping to Availability Service `MonthAvailability`

| Source signal | Proposed status |
|---------------|-----------------|
| `price` + `status=monthly` | `AVAILABLE` |
| `price` + `status=prorated` | `AVAILABLE` or `REQUIRES_CONFIRMATION` |
| `insufficient_data`, segment < 5 days | `UNAVAILABLE` if calendar shows no availability |
| `insufficient_data`, no calendar | `UNKNOWN` |
| `fetch_price` None, "даты заняты" | `UNAVAILABLE` |

Currency: parser targets THB via URL currency resolution; prices rounded to hundreds THB.

## Reuse recommendation

**Preferred: Option A — shared library**

Extract to e.g. `shared/openhome_airbnb/`:

- `monthly_pricing.py` (already pure, no I/O)
- `availability.py` (Playwright calendar fetch)
- `calendar_status.py` (new: map calendar + price entry → AVAILABLE/UNAVAILABLE/UNKNOWN)
- `parser_bridge.py` — thin wrapper around `AirbnbParser.fetch_price_for_period` (Selenium stays in Agent1 package initially)

Availability Service `AirbnbAvailabilityProvider` imports shared module; Agent1 imports same module to avoid drift.

**Avoid:** importing Agent2 or depending on Agent2 process.

**Avoid:** copy-pasting all of `airbnb_parser.py` (600+ lines Selenium) without shared module plan.

## Future provider interface (not live yet)

```python
class AirbnbAvailabilityProvider:
    def fetch_month_availability(
        self,
        source_url: str,
        start_month: date,  # first day of effective window month
        months: int = 12,
    ) -> list[MonthAvailability]:
        ...
```

Internally: `get_effective_window_start` alignment + `iter_months_ahead` / `build_month_window` month keys.

## Config alignment note

- Agent1 `iter_months_ahead` starts at **next calendar month** (same semantics as `AVAILABILITY_WINDOW_START_MODE=next_month`).
- Agent2 `price_months_ahead` in pipeline.json is **3** — Availability Service rolling window is **12**. Provider must not assume Agent2 config.
