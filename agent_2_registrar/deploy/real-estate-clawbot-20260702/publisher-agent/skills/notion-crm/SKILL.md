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
| `video_url_vertical` | 9:16 — Reels, TikTok, Shorts |
| `video_url_square` | 1:1 — IG Feed, FB square |
| `video_url_wide` | 16:9 — YouTube, FB landscape |

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

## Обновить статус

```bash
curl -s -X PATCH "https://api.notion.com/v1/pages/${PAGE_ID}" \
  -H "Authorization: Bearer ${NOTION_API_KEY}" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{
    "properties": {
      "Status": {
        "status": { "name": "Взял на постинг" }
      }
    }
  }'
```

## После успешной публикации

Обнови:
- `Status` → `Запланировано`
- `publora_post_group_id` → rich_text или url (как настроено в базе)
- Очисти `publish_error`

## При ошибке

- Верни `Status` на предыдущее значение
- Запиши текст ошибки в `publish_error`

## Notion API refs

- [Update page](https://developers.notion.com/reference/patch-page)
- [Status property](https://developers.notion.com/reference/page-property-values#status)
