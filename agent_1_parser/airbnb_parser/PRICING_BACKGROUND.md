# Background monthly pricing (Airbnb)

## Overview

1. **Primary month** — blocking/high priority, collected before Agent 2 handoff  
2. **Months 2–12** — non-blocking jobs in a persistent JSON queue  
3. **One global background worker** (configurable, default `1`)  
4. **Fair round-robin** across objects by `month_depth`  
5. **Retry later** — failed/blocked months re-enter the queue with backoff  

Agent 3 reads the first upcoming month from `monthly_prices` / Notion as before.

## Config

`agent_2_registrar/_import/assistant-media/config/pipeline.json`:

```json
"price_months_ahead": 12,
"monthly_pricing": {
  "months_ahead": 12,
  "background_workers": 1,
  "background_request_delay_seconds": {"min": 5, "max": 12},
  "block_cooldown_seconds": [300, 900, 3600],
  "retry_delay_seconds": [120, 600, 1800],
  "stale_running_seconds": 300,
  "max_job_attempts": 3
}
```

Env overrides: `PRICE_MONTHS_AHEAD`, `PRICE_BACKGROUND_WORKERS`, `PRICE_QUEUE_PATH`.

## Queue state

File: `data/pricing_queue.json`

Each job: `object_id`, `listing_url`, `month`, `status` (`pending|running|done|retry|insufficient_data|blocked`), `month_depth`, `attempt`, `next_retry_at`.

Per-object state: `monthly_prices`, calendar snapshot, `pricing_status` (`collecting|partial|complete`), counters.

## Recovery

On startup the worker loads the queue, resets stale `running` jobs to `retry`, skips `done` months, continues `pending/retry/blocked` (after cooldown).

## Mac worker

`scripts/price_worker_local.py` remains for **manual recovery / diagnostics** — not required for the main 12-month server flow.

## Calendar

One `fetch_calendar_days()` per object at primary collection; snapshot stored on the object and reused for all background months.

**Before:** 1 calendar + up to 3×N parallel price loads + refill pass.  
**After:** 1 calendar at primary + 1 sequential background request per job (with delay).
