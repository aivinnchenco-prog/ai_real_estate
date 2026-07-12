---
name: notion-crm
description: Reads and updates property records in Notion CRM database — video URLs, status, captions. Use when fetching listings from Notion, updating publish status, or querying the CRM queue for social posting.
metadata: {"openclaw": {"requires": {"bins": ["curl"], "env": ["NOTION_API_KEY", "NOTION_DATABASE_ID"]}}}
---

# Notion CRM

Database: [CRM](https://app.notion.com/p/e817ce50e78849928b86c9c9fc1fbcf7)

## Поля видео

| Property | Формат |
|----------|--------|
| `video_url_vertical` | 9:16 — FFmpeg reel (Agent 3) |
| `video_url_Seedance` | 9:16 — Higgsfield Seedance (Agent 5) |

## Получить запись

```bash
curl -s "https://api.notion.com/v1/pages/${PAGE_ID}" \
  -H "Authorization: Bearer ${NOTION_API_KEY}" \
  -H "Notion-Version: 2022-06-28"
```

Извлеки URL: `properties.{field}.url`

## Очередь — готовые к публикации

```bash
curl -s -X POST "https://api.notion.com/v1/databases/${NOTION_DATABASE_ID}/query" \
  -H "Authorization: Bearer ${NOTION_API_KEY}" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{
    "filter": {
      "property": "Статус",
      "status": { "equals": "ready_to_post" }
    }
  }'
```

> Имена полей настраиваются в `config/publisher.json`

## Обновить статус (Agent 6)

```bash
curl -s -X PATCH "https://api.notion.com/v1/pages/${PAGE_ID}" \
  -H "Authorization: Bearer ${NOTION_API_KEY}" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{
    "properties": {
      "Статус": {
        "status": { "name": "video_in_progress" }
      },
      "agent6_locked": { "checkbox": true }
    }
  }'
```

## После успешной публикации

Обнови:
- `Статус` → `ready_to_post` (или оставить; главное — `agent6_locked=true`)
- `metricool_post_id` → rich_text
- **TG:** `Описание для Telegram` → `post_url_telegram`
- **Соцсети:** `Описание сец.сети` → Metricool → `post_url_*`
  - **Сразу:** ссылка на календарь Metricool (planner `Copy link`)
  - **После выхода поста:** перезапись на URL публикации в соцсети
- `agent6_carousel_done` / `agent6_video_done` по типу поста
- Очисти `last_error`

Проверка схемы: `python3 scripts/verify_notion_schema.py`

Если пост запланирован на будущее, ссылка подтягивается **автоматически через 5 минут после времени публикации**.  
Пост в Metricool ищется по **`Объект ID`** в тексте (`20260702_001`, `#20260702_001`, `№20260702_001`) — даже если вы пересоздали пост вручную.  
Вручную: `python3 scripts/sync_post_urls.py --page-id PAGE_ID --platform linkedin`.

## При ошибке

- Верни `Status` на предыдущее значение
- Запиши текст ошибки в `publish_error`

## Notion API refs

- [Update page](https://developers.notion.com/reference/patch-page)
- [Status property](https://developers.notion.com/reference/page-property-values#status)
