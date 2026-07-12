---
name: metricool-mcp
description: Use Metricool via official MCP server for analytics, competitor data, and post scheduling. Use when running in Cursor, OpenClaw, Clawbot, or Hermes with MCP enabled — not for headless cron (use publish_pipeline.py instead).
metadata: {"openclaw": {"requires": {"bins": ["uvx"], "env": ["METRICOOL_USER_TOKEN", "METRICOOL_USER_ID"]}, "mcp": {"server": "metricool", "package": "mcp-metricool"}}}
---

# Metricool MCP

Official server: `uvx --upgrade mcp-metricool`  
Setup: `docs/METRICOOL_MCP.md`

## When to use MCP vs scripts

| Task | Use |
|------|-----|
| Production publish Notion → Metricool + locks | `python3 scripts/publish_pipeline.py` |
| Telegram first, then carousel chain | `python3 scripts/publish_telegram.py` |
| Analytics, best times, competitor compare | **MCP tools** |
| Coordinator agent on Clawbot/OpenClaw | **MCP tools** or `exec` scripts |

## Auth (same as REST)

- `METRICOOL_USER_TOKEN` → header `X-Mc-Auth` (inside MCP server)
- `METRICOOL_USER_ID` — env on server
- `blogId` — pass per tool call (`METRICOOL_BLOG_ID` from `.env`)

## Agent 6 content rules

- Caption for all social networks: Notion **«Описание сец.сети»**
- Never use column **«Описание»** for publishing
- Photos: Notion **«Фото»** (R2 gallery URL)

## Example prompts (Cursor / coordinator)

- «Schedule Instagram carousel for blogId X using caption from Notion page …»
- «Which Reel had best engagement last month for blogId …?»
- «Compare my TikTok vs competitors last week»

## Config files

- Cursor: `config/metricool-mcp.cursor.example.json`
- OpenClaw: `config/openclaw.mcp-metricool.example.json`
- Full multi-agent: `deploy/openclaw.agent6-publisher.example.json`
