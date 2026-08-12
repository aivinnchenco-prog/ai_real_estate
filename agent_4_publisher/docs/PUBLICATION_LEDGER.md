# Publication Ledger (Agent 4)

Canonical durable history for PostMyPost publications.

**Owner:** Agent 4 Publisher  
**Path:** `agent_4_publisher/data/publications.sqlite3` (gitignored)  
**Consumers:** Agent 10 (read-only, Instagram + Facebook only)

## OLD vs NEW

| | Model |
|---|---|
| **OLD** | `object + slot → current publication_id` (`data/postmypost_publications.json`) |
| **NEW** | `object → many publication history rows` (SQLite ledger) |

Current JSON pointer is **kept** for deferred sync / latest convenience. Ledger is append/idempotent history.

## Schema (`publications`)

| Column | Notes |
|---|---|
| `publication_key` | Internal UUID (immutable) |
| `object_id` | Notion Объект ID |
| `notion_page_id` | Notion page |
| `postmypost_publication_id` | **UNIQUE** — `id` from `POST /publications` |
| `platform` / `format` / `slot` | e.g. `instagram` / `reel` / `instagram:reel` |
| `account_id` | PostMyPost account |
| `scheduled_at` / `created_at` / `published_at` | `published_at` stays NULL until confirmed source exists |
| `status` / `raw_status` | Normalized only for known `1=published`, `5=pending` |
| `external_media_id` / `permalink` | From GET `posts[]`; nullable; planner URL never used as permalink |
| `source` / `last_synced_at` | provenance |

Idempotency: **same PostMyPost id → same row** (no unique on object/slot).

## Lifecycle

```
POST /publications → id
  → ledger.register (immediate)
  → current JSON pointer (slot overwrite OK)
  → Notion current fields (metricool_post_id, post_url_*)
  → deferred GET /publications/{id}
  → ledger.enrich (same row: status, permalink, external_media_id)
  → Notion URL update if ready
```

Ledger write failure after successful POST: **critical log**, `ledger_error` on result, **no DELETE** of external publication.

## Reconciliation

```bash
python3 scripts/reconcile_publication_ledger.py
```

Imports known `publication_id` + `object_id` + platform from current JSON only. No network.

## Future

Table `publication_analytics_snapshots` (24h/72h/7d) can be added later — not created until PostMyPost analytics contract is confirmed.
