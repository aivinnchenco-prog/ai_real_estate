# Tools — Publisher

## Notion CRM

- Database: `https://app.notion.com/p/e817ce50e78849928b86c9c9fc1fbcf7`
- Database ID: `NOTION_DATABASE_ID` (из .env)
- Поля видео: `video_url_vertical`, `video_url_Seedance`

## Publora

- Dashboard: https://app.publora.com/
- API base: `https://api.publora.com/api/v1`
- Docs: https://docs.publora.com/

## Env

| Variable | Назначение |
|----------|------------|
| `NOTION_API_KEY` | Integration token |
| `NOTION_DATABASE_ID` | ID базы CRM |
| `PUBLORA_API_KEY` | API key из Publora dashboard |
| `PUBLORA_PLATFORM_INSTAGRAM` | platformId, напр. `instagram-178414...` |
| `PUBLORA_PLATFORM_TIKTOK` | platformId |
| `PUBLORA_PLATFORM_YOUTUBE` | platformId |
| `PUBLORA_PLATFORM_FACEBOOK` | platformId |
| `NOTION_STATUS_READY` | Статус «готов к публикации» |
| `NOTION_STATUS_TAKEN` | Статус «взял на постинг» |
| `NOTION_STATUS_SCHEDULED` | Статус «запланировано» |

## Config

`config/publisher.json` — имена полей Notion, маппинг платформ

## Skills

- `notion-crm`
- `publish-publora`

## Script

`workspaces/publisher/scripts/publish_pipeline.py`
