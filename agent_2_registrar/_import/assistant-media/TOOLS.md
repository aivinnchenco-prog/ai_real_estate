# TOOLS.md — Real Estate Pipeline

## STRICT: инструменты для объявлений

### Разрешено

| Инструмент | Для чего |
|------------|----------|
| **Shell** | Только скрипты из whitelist ниже |
| **Read/Write файлов** | `data/sessions/{id}/description.txt`, `photos/*` |

### Запрещено для объявлений

| Инструмент | Почему |
|------------|--------|
| **Browser** | Парсинг Airbnb — не твоя задача |
| **web_fetch на URL объявлений** | То же |
| **LLM-выдумывание полей** | Поля заполняет `agent2_structurize.py` |

---

## Whitelist shell-команд

```bash
python3 scripts/media_batch_gate.py init|bump|ready|status --session ...
python3 scripts/agent2_structurize.py --session ... --source "..."
node scripts/agent3_video.mjs --object-id ...
./scripts/run_pipeline.sh --session ... --source "..."
python3 tests/test_pipeline.py -v
curl -s http://127.0.0.1:8077/health
```

Полный пайплайн предпочтительнее отдельных шагов:

```bash
./scripts/run_pipeline.sh --session SESSION_ID --source "Airbnb https://..."
```

---

## Env

`.env.real-estate` в корне workspace.

## Notion

Database: `e817ce50-e788-4992-8b86-c9c9fc1fbcf7`

## R2

Prefix: `{object_id}/photos/`, `{object_id}/video_9x16.mp4`, `{object_id}/video_seedance_9x16.mp4`

## Сервисы 24/7

```bash
./start.sh                      # curator + chain watcher (docker compose)
./scripts/healthcheck_vps.sh
```

При `run_pipeline.sh` сервисы поднимаются сами. См. `deploy/VPS_24X7.md`.

## Higgsfield Seedance (Agent 3 track B)

| Способ | Когда | Настройка |
|--------|-------|-----------|
| **CLI** (рекомендуется на VPS 24/7) | `seedance_2_0`, до 9 ref-фото за batch | `./scripts/setup_vps_24x7.sh` → `./scripts/higgsfield_auth_remote.sh` |
| **MCP** | Cursor / Claude / OpenClaw | URL: `https://mcp.higgsfield.ai/mcp` + OAuth |
| **API Key** | DoP/motions only, не Seedance 2.0 | `HIGGSFIELD_API_KEY` + `HIGGSFIELD_API_SECRET` в `.env` |

Проверки:

```bash
node scripts/test_higgsfield_auth.mjs      # API key → motions
higgsfield auth token                      # CLI OAuth
node scripts/test_higgsfield_providers.mjs # какой provider выберет Agent 3
```

Cursor MCP example: `config/higgsfield-mcp.cursor.example.json`

- Photos: `{object_id}/photos/` + `index.html`
- Videos: `{object_id}/video_9x16.mp4`, `{object_id}/video_seedance_9x16.mp4`
- Music: `music/track_001.mp3` …

## Config

`config/pipeline.json`
