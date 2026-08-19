# Availability Service

Isolated service for property availability: **Daily Availability DB** (exact dates), month summaries for Notion, and exact-stay matching.

## Source of truth

| Layer | Role |
|-------|------|
| **Daily Availability DB** (`availability_calendar_days`) | Source of truth for exact open/blocked dates (`AVAILABLE` / `BLOCKED` / `UNKNOWN`) |
| **MonthAvailability** | Summary + price for Notion month columns and UI |
| **Exact StayMatcher** | Reads daily DB only; price and month display status do not affect exact stay |

Price does **not** affect exact daily availability. Month display status does **not** affect `Exact StayMatcher`.

## Website month filter (V1)

For the site month-range filter (`app/month_filter.py`):

| Month display status | Filter result |
|----------------------|---------------|
| `FULLY_AVAILABLE` | required for MATCH |
| `PARTIAL` | NO MATCH |
| `UNAVAILABLE` | NO MATCH |
| `UNKNOWN` | NO MATCH |

**Rule:** an object is shown only if **every** month in the user-selected period is `FULLY_AVAILABLE`.

Example: Dec = `FULLY_AVAILABLE`, Jan = `PARTIAL`, Feb = `FULLY_AVAILABLE` → query “Dec + 3 months” → **NO MATCH**.

Availability for the filter comes from the daily calendar (`evaluate_month_display_status`), not from price or `pricing_status`.

## Monthly pricing semantics (Agent1 `monthly_pricing.py`)

Prices are fetched via `AirbnbParser.fetch_price_for_period()` and normalized in `price_for_month()`.

| `pricing_status` | Requested period | What Airbnb returns | Normalization | Stored `price` |
|------------------|------------------|---------------------|---------------|----------------|
| `monthly` | Full calendar month (`1st` → last day) | Total for that stay window (or monthly rate UI) | `round_to_hundreds()` only | Estimated **full-month** THB |
| `prorated` | Longest available segment in month (≥ 5 days) | Total cost for that segment | `(segment_total ÷ based_on_days) × 30`, rounded to hundreds | **Extrapolated 30-day** monthly estimate, not segment total |
| `insufficient_data` | No segment ≥ 5 days, or Airbnb returned no price | — | — | `NULL` |

`based_on_days` = inclusive days in the priced segment (`period_used`), used as the denominator in prorated extrapolation.

**Parser nuance:** if Airbnb shows “X ฿ помесячно” on a short window (&lt; 27 nights), `fetch_price_for_period` converts the displayed monthly rate to segment total (`rate × days ÷ 30`) before `extrapolate_price` scales back to 30 days.

Display status (`FULLY_AVAILABLE` / `PARTIAL` / …) is always from the daily calendar, never inferred from price presence.

## SAFE SERVER MODE (VPS batch)

First production batch: **5 objects sequential**, no parallel calendar browsers.

```bash
python3 -m availability_service.main batch-safe \
  --object-id A_001,A_002,A_003,A_004,A_005 \
  --confirm-live \
  --confirm-write
```

Per object: **calendar → pricing → SQLite → Notion** (existing row only).

```
AVAILABILITY_OBJECT_CONCURRENCY=1
AVAILABILITY_CALENDAR_CONCURRENCY=1   # process lock — never two calendar browsers
AVAILABILITY_PRICE_CONCURRENCY=1
AVAILABILITY_BROWSER_MAX_INSTANCES=2
AVAILABILITY_BATCH_SAFE_MAX_OBJECTS=5
```

`app/server_concurrency.py` enforces calendar single-flight and browser caps.  
After batch, metrics + **manual** recommendation on `PRICE_CONCURRENCY=2` — nothing auto-raised; calendar stays 1 until separate approval.

## Run

```bash
python3 -m availability_service.main dry-run
```

Local runtime defaults to `availability_service/data/` when `/opt/openhome` is absent.

Production SQLite: `/opt/openhome/runtime/availability/availability.sqlite3`

**Availability table window (fixed):** September 2026 → August 2027 (`Sep 26` … `Aug 27` in Notion).  
Set `AVAILABILITY_WINDOW_START_MODE=sep_2026_aug_2027` (default).

**Column order (frozen):** month columns first, technical fields at the end — see `app/target_column_order.py`. The service never reorders Notion schema via API; display/readback/writes follow `LOCKED_TARGET_COLUMN_ORDER`.

## Env

```
AVAILABILITY_ENABLED=false
AVAILABILITY_DRY_RUN=true
AVAILABILITY_SOURCE_NOTION_DATABASE_ID=
AVAILABILITY_TARGET_NOTION_DATABASE_ID=
AVAILABILITY_RUNTIME_DIR=/opt/openhome/runtime/availability
```

## Internal interfaces (not wired yet)

- `AvailabilityStayService.get_stay_availability_for_agent()` — Agent6-ready payload
- `AvailabilityStayService.get_calendar_view()` — website calendar slice

## Tests

```bash
python3 -m pytest availability_service/tests -q
```
