# Исправления пайплайна (2026-07-01)

## Найденные ошибки

| # | Проблема | Было | Стало |
|---|----------|------|-------|
| 1 | Ожидание фото | Один раз 150 сек | **150 сек после КАЖДОЙ пачки** (`media_batch_gate.py`) |
| 2 | Agent 3 раньше времени | Запуск без проверки Notion | Только при статусе `ready_for_video` |
| 3 | Хардкод `property-001` | orchestrator брал чужие фото | R2 list по `{object_id}/photos/` |
| 4 | Notion video поля | Старые field ID (`pMM;`…) | `video_url_vertical`, `video_url_Seedance` |
| 5 | Google Maps / Источник | Пропускались | Обязательны в `agent2_structurize.py` |
| 6 | Район / адрес | Не извлекались | Парсинг из описания + Google Maps URL |
| 7 | Zoom эффект | Только zoom in | **Random zoom in/out** per кадр |
| 8 | Мин. длина видео | Могло быть <15 сек | Min 6 кадров × 2.5 сек = 15 сек |
| 9 | API keys в коде | orchestrator.mjs, cli.sh | Из `.env.real-estate` |
| 10 | Agent 6 рано | Нет gate | Только после `READY_TO_POST` |

## Новые скрипты

```
scripts/media_batch_gate.py   — ожидание пачек фото
scripts/agent2_structurize.py — Notion + R2 + Maps
scripts/agent3_video.mjs      — curator + render + Notion
scripts/run_pipeline.sh       — полный цикл с gate
config/pipeline.json          — конфиг полей и статусов
```

## Правильный порядок

```
Parser (TG) → описание + фото пачками
    → media_batch_gate (150s после каждой пачки)
    → agent2_structurize (Notion + R2, ready_for_video)
    → agent3_video (видео 3 формата, ready_to_post)
    → publish_pipeline (Agent 6, Publora)
```

## Обновление (2026-07-02)

| # | Улучшение | Файлы |
|---|-----------|-------|
| 11 | **One session = one object_id** | `session_store.py`, `session.json` |
| 12 | R2 SigV4 upload | `real_estate_handler.py` |
| 13 | Google Maps → карточка проекта (place_id/cid) | `maps_resolver.py` |
| 14 | Галерея index.html вместо одного фото | `gallery_html.py` |
| 15 | Тип жилья, Вид, Залог, Удобства, Тип аренды | `listing_parser.py` |
| 16 | Agent 3: video_failed + error_count + curator fallback | `agent3_video.mjs` |
| 17 | Gate: счётчик фото с диска | `media_batch_gate.py` |
| 18 | Event log jsonl | `event_log.py`, `data/events/` |
| 19 | run_pipeline: --from-step, --publish | `run_pipeline.sh` |
| 20 | Unit tests | `tests/test_pipeline.py` |
| 21 | Curator /health + docker-compose | `curator_service.py`, `docker-compose.yml` |
| 22 | Security: gitignore .env.real-estate, redact secrets | `.gitignore`, legacy upload scripts |
| 23 | Только 9:16 FFmpeg, cover-crop zoom | `reelFilters.mjs`, `renderReel.mjs` |
| 24 | Seedance track B (13 фото, Higgsfield CLI/MCP) | `renderSeedance.mjs`, `curator_diverse.py` |
| 25 | Agent 3 parallel: reel + Seedance одновременно | `agent3_video.mjs` |
| 26 | 24/7 в проекте: docker compose + start.sh | `docker-compose.yml`, `start.sh` |
| 27 | Session handoff | `SESSION_HANDOFF.md` |
| 28 | Notion «Цена за месяц» format baht→number | API PATCH database |
| 29 | Отдельный Higgsfield-агент (архив) | `export/higgsfield-seedance-agent/`, Desktop `.tar.gz` |
