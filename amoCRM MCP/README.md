# amoCRM Read-Only MCP

Isolated **read-only** MCP server for ChatGPT to analyze real amoCRM structure and deals.

## Scope

- **GET only** against amoCRM API v4
- No write/update/delete tools
- No imports from Agents 1–9
- Separate credentials namespace: `AMO_MCP_*`

## Architecture

```
ChatGPT (custom MCP app)
        |
        | HTTPS + Bearer MCP_API_TOKEN
        v
amoCRM MCP (Streamable HTTP /mcp)
        |
        | GET only guard
        v
amoCRM API v4 (https://{subdomain}.amocrm.ru/api/v4)
```

## MCP stack

- Python SDK: `mcp` (`MCPServer`)
- Transport: **Streamable HTTP** (`/mcp`)
- Optional local dev: `stdio`

## Endpoints

| Path | Purpose |
|------|---------|
| `GET /health` | Liveness (`{"status":"ok"}`) |
| `/mcp` | MCP Streamable HTTP endpoint |

Production URL example: `https://mcp.your-domain.com/mcp`

## Auth

### amoCRM

`.env`:

```env
AMO_MCP_SUBDOMAIN=your-subdomain
AMO_MCP_ACCESS_TOKEN=...
```

Reference in repo: `agent_6_qualifier` uses `AMO_SUBDOMAIN` + `AMO_ACCESS_TOKEN` (write-capable). MCP uses a **separate** namespace so ChatGPT access can be scoped independently.

### MCP endpoint (ChatGPT → server)

```env
MCP_API_TOKEN=long-random-secret
```

ChatGPT custom app should send:

`Authorization: Bearer <MCP_API_TOKEN>`

`/health` is open; `/mcp` requires bearer token.

OAuth/OIDC can be added later on top of the same ASGI app.

## Read-only guarantee

`ReadOnlyAmoClient.ALLOWED_METHODS = {"GET"}`

Any `POST/PATCH/PUT/DELETE` raises `ReadOnlyViolation` **before** HTTP request.

## Tools (all READ-ONLY)

| Tool | Args |
|------|------|
| `amo_get_account` | `include_raw=false` |
| `amo_list_users` | `limit`, `include_raw` |
| `amo_get_user` | `user_id`, `include_raw` |
| `amo_list_pipelines` | `include_raw` |
| `amo_get_pipeline` | `pipeline_id`, `include_raw` |
| `amo_list_lead_fields` | `include_raw` |
| `amo_list_contact_fields` | `include_raw` |
| `amo_search_leads` | `query`, `pipeline_id`, `status_id`, `responsible_user_id`, `page`, `limit`, `include_raw` |
| `amo_get_lead` | `lead_id`, `include_raw` |
| `amo_get_lead_links` | `lead_id`, `include_raw` |
| `amo_search_contacts` | `query`, `page`, `limit`, `include_raw` |
| `amo_get_contact` | `contact_id`, `include_raw` |
| `amo_get_contact_chats` | `contact_id`, `include_raw` |
| `amo_list_tasks` | `entity_id`, `responsible_user_id`, `is_completed`, `task_type`, `page`, `limit`, `include_raw` |
| `amo_get_lead_tasks` | `lead_id`, `limit`, `include_raw` |
| `amo_get_lead_notes` | `lead_id`, `page`, `limit`, `include_raw` |
| `amo_get_deal_overview` | `lead_id`, `notes_limit=10`, `include_raw` |

Default `limit=20`, hard max `50`. `include_raw=false` by default.

## amoCRM GET endpoints used

| Tool area | Endpoint |
|-----------|----------|
| Account | `GET /api/v4/account` |
| Users | `GET /api/v4/users`, `GET /api/v4/users/{id}` |
| Pipelines | `GET /api/v4/leads/pipelines`, `GET /api/v4/leads/pipelines/{id}` |
| Lead fields | `GET /api/v4/leads/custom_fields` |
| Contact fields | `GET /api/v4/contacts/custom_fields` |
| Leads | `GET /api/v4/leads`, `GET /api/v4/leads/{id}` |
| Links | `GET /api/v4/leads/{id}/links` |
| Contacts | `GET /api/v4/contacts`, `GET /api/v4/contacts/{id}` |
| Contact chats | `GET /api/v4/contacts/chats?contact_id=` |
| Tasks | `GET /api/v4/tasks` |
| Notes | `GET /api/v4/leads/{id}/notes`, fallback `GET /api/v4/leads/notes?filter[entity_id]=` |

## Chats API (read-only status)

**Implemented now**

- `GET /api/v4/contacts/chats` — returns `contact_id` ↔ `chat_id` bindings (official amoCRM/Kommo API)

**Not implemented (blocker for v1)**

- `amo_get_chat` / `amo_get_chat_history` — amoCRM **Chats API** (amojo) uses a separate integration contract (channel onboarding, scoped tokens, often write-oriented webhooks). With standard CRM OAuth/token only the **binding list** is reliably available read-only.
- Message history requires Chats API credentials and channel registration; documented for future Conversation Router work.

Future target architecture (analysis only):

```
one deal
├── CLIENT contact
│   ├── Telegram chat binding
│   └── WhatsApp chat binding
└── OWNER contact
    ├── Telegram chat binding
    └── WhatsApp chat binding
```

## Local setup

```bash
cd "amoCRM MCP"
cp .env.example .env
pip install -r requirements.txt
PYTHONPATH=src python3 scripts/diagnose.py
bash scripts/run_local.sh
```

ASGI app with `/health` + auth:

```bash
PYTHONPATH=src uvicorn amocrm_mcp.asgi:create_app --factory --host 127.0.0.1 --port 8787
```

## Deploy (VPS)

See `deploy/`:

- `amocrm-mcp.service` — systemd unit
- `amocrm-mcp.env.example` — environment file
- `nginx.example.conf` — TLS reverse proxy

Requirements: TLS in production, private `MCP_API_TOKEN`, firewall.

## CHATGPT SETUP

Per current OpenAI custom MCP flow:

1. Deploy remote MCP to HTTPS (`https://<host>/mcp`)
2. Enable Developer Mode in ChatGPT (plan-dependent)
3. Settings → Apps → Create custom app
4. Enter MCP endpoint URL
5. Configure auth (`Bearer` with `MCP_API_TOKEN`)
6. Scan tools — verify only `amo_*` read-only tools appear
7. Complete authorization if prompted
8. Enable app in workspace
9. In chat, ask ChatGPT to audit pipelines/deals using MCP tools

### OpenAI plan note

- Business / Enterprise / Edu: broader MCP support in developer mode
- Pro: custom MCP often read/fetch oriented
- **This server is intentionally READ-ONLY** — suitable for analysis on any plan that allows read tools

## Tests

```bash
cd "amoCRM MCP"
PYTHONPATH=src pytest tests/ -q
```

All offline mocks — no production amoCRM writes.

## PII / logging

- CRM payloads are returned to ChatGPT as requested data
- Application logs do not dump full CRM responses
- Bearer tokens redacted in errors/logs

## Not connected automatically

After implementation we **do not** auto-connect ChatGPT. Next manual steps:

1. Deploy MCP
2. Verify HTTPS + auth
3. Connect in ChatGPT
4. Run read-only CRM audit
5. Later design write paths (Conversation Router) separately
