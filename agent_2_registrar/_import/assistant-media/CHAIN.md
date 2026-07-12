# CHAIN.md — цепочка агентов (Notion CRM)

Каждый агент **ждёт сигнал в Notion** от предыдущего. Не перескакивать этапы.

```
Agent 1 Parser     → описание + фото в session (внешний)
        ↓
Gate + Agent 2     → Notion: ready_for_video + галерея R2 в «Фото »
        ↓
Agent 3 Video      → Notion: ready_to_post + video_url_vertical (сразу после FFmpeg reel)
                   → video_url_Seedance дописывается позже (Agent 5 Seedance, параллельно)
        ↓
Agent 6 Publisher  → Notion: published + publora_post_group_id
```

## Триггеры

| Агент | Когда запускается | Что проверяет в Notion |
|-------|-------------------|------------------------|
| **2** | Gate ready + session | — |
| **3** | Статус `ready_for_video` | Есть URL галереи в «Фото », нет видео |
| **5** | Параллельно после Agent 3 | Фото в R2 → `video_url_Seedance` |
| **6** | Статус `ready_to_post` | Есть `video_url_vertical` (Seedance — опционально) |

Agent 3 **не запускается** пока Agent 2 не записал галерею.  
Agent 6 **не запускается** пока Agent 3 не записал видео.

## Автопродолжение

После Agent 2 (`agent2_structurize.py`) автоматически вызывается:

```bash
python3 scripts/chain_runner.py --from-agent 3 --object-id {id}
```

## Фоновый watcher (встроен в проект)

На сервере сервисы поднимаются **вместе с проектом**:

```bash
./start.sh                    # curator + chain watcher (docker compose)
# или при любом run_pipeline — ensure_services.sh сам поднимет сервисы
```

`chain` контейнер крутит `chain_runner.py --watch` — каждые 30 сек Notion → Agent 3/6.  
Seedance: Higgsfield CLI внутри контейнера (OAuth один раз: `docker compose exec chain higgsfield auth login` или SSH-туннель).

Подробнее: `deploy/VPS_24X7.md`

## Ручной запуск Agent 3

```bash
python3 scripts/chain_runner.py --from-agent 3 --object-id 20260701_001
```

Скрипт сам проверит Notion перед `agent3_video.mjs`.

## Agent 6 (Publisher)

```bash
python3 scripts/chain_runner.py --from-agent 6 --object-id 20260701_001 --publish instagram
```

Или в `config/pipeline.json`: `"auto_agent6": true` — тогда Agent 6 идёт сразу после Agent 3.

> `--from-agent 4` и `"auto_agent4"` поддерживаются как deprecated-алиасы.

## Конфиг

`config/pipeline.json` → секция `chain`:

```json
{
  "poll_interval_seconds": 30,
  "auto_continue_after_agent2": true,
  "auto_agent6": false,
  "publish_platforms": ["instagram"],
  "publisher_script": null
}
```

Скрипт ищет `publish_pipeline.py` в:
- `workspaces/publisher/` (legacy в монорепо)
- `agent_6_piblich_social/real-estate-agent6-publisher/` (standalone)

## Логи

`data/events/{object_id}.jsonl` — события chain/agent2/agent3/agent6.
