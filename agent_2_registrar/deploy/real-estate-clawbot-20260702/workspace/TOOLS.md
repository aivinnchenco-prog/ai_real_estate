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

- Photos: `{object_id}/photos/` + `index.html`
- Videos: `{object_id}/video_1x1.mp4`, `video_3x4.mp4`, `video_9x16.mp4`
- Music: `music/track_001.mp3` …

## Config

`config/pipeline.json`
