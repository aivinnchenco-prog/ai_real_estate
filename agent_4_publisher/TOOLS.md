# Tools — Agent 6 Publisher

## Notion CRM

- Database: `https://app.notion.com/p/e817ce50e78849928b86c9c9fc1fbcf7`
- Database ID: `NOTION_DATABASE_ID` (из .env)
- Поля видео: `video_url_vertical`, `video_url_Seedance`

## Metricool

- Dashboard: https://app.metricool.com/
- API base: `https://app.metricool.com/api`
- Docs: https://app.metricool.com/resources/apidocs/index.html
- **MCP:** `docs/METRICOOL_MCP.md` — Cursor, OpenClaw, Clawbot, Hermes
- Plan: Advanced or higher for API

## Env

| Variable | Назначение |
|----------|------------|
| `NOTION_API_KEY` | Integration token |
| `NOTION_DATABASE_ID` | ID базы CRM |
| `METRICOOL_USER_TOKEN` | API token (Account Settings → API) |
| `METRICOOL_USER_ID` | userId из URL бренда |
| `METRICOOL_BLOG_ID` | blogId из URL бренда |
| `METRICOOL_TIMEZONE` | Часовой пояс планировщика, напр. `Asia/Bangkok` |
| `NOTION_STATUS_READY` | Статус «готов к публикации» |
| `NOTION_STATUS_TAKEN` | Статус «взял на постинг» |
| `NOTION_STATUS_SCHEDULED` | Статус «запланировано» |

## Config

`config/publisher.json` — имена полей Notion, маппинг платформ, `metricool_platform_settings`

## Skills

- `notion-crm`
- `publish-metricool` — REST / скрипты (продакшн)
- `metricool-mcp` — MCP tools (оркестратор, аналитика)
- `chatplace-funnel` — ChatPlace MCP (phase 5, после тестов)

## Scripts

- `scripts/publish_pipeline.py` — основной пайплайн
- `scripts/list_brands.py` — проверка бренда Metricool
