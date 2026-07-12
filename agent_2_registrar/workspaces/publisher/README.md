# Agent 6 Publisher — Notion → Publora

Standalone: `agent_6_piblich_social/real-estate-agent6-publisher/`

## Быстрый старт

```bash
# 1. Секреты
cp ../../.env.example ../../.env
# NOTION_API_KEY, NOTION_DATABASE_ID, PUBLORA_API_KEY

# 2. Platform IDs из Publora
python3 scripts/list_connections.py

# 3. Dry-run
python3 scripts/publish_pipeline.py \
  --page-id YOUR_NOTION_PAGE_ID \
  --platform instagram \
  --dry-run

# 4. Публикация (+30 мин по умолчанию)
python3 scripts/publish_pipeline.py \
  --page-id YOUR_NOTION_PAGE_ID \
  --platform instagram \
  --schedule "2026-07-02T14:00:00.000Z"

# 5. Очередь
python3 scripts/publish_pipeline.py --queue --platform tiktok
```

## Notion setup

1. Создай [Notion Integration](https://www.notion.so/my-integrations)
2. Подключи integration к [базе CRM](https://app.notion.com/p/e817ce50e78849928b86c9c9fc1fbcf7)
3. Добавь поля (если нет): `video_url_vertical`, `video_url_Seedance`, `publora_post_group_id`, `publish_error`
4. Статусы: `Готов к публикации` → `Взял на постинг` → `Запланировано`

## Publora setup

1. Аккаунт на https://app.publora.com/
2. Подключи соцсети в dashboard (OAuth)
3. API key: Settings → API
4. Docs: https://docs.publora.com/getting-started

## Маппинг видео

| Платформа | Поле Notion |
|-----------|-------------|
| все платформы (по умолчанию) | `video_url_vertical` |
| опционально (если настроено) | `video_url_Seedance` |

Настраивается в `config/publisher.json`.
