---
name: chatplace-funnel
description: ChatPlace MCP funnel for Instagram — comment trigger sends Telegram post link. Use after post_url_telegram and live IG URL. Requires CHATPLACE_API_KEY and chatplace MCP server.
metadata: {"openclaw": {"requires": {"env": ["CHATPLACE_API_KEY"]}, "mcp": {"server": "chatplace", "url": "https://mcp.chatplace.io/mcp"}}}
---

# ChatPlace funnel — **только через MCP**

**Spec:** `docs/CHATPLACE_FUNNEL.md`  
**Client:** `scripts/chatplace_mcp.py`  
**Entry:** `scripts/setup_chatplace_funnel.py`

## Когда запускать

После:
- `post_url_telegram` заполнен
- `post_url_instagram_carousel` / `post_url_instagram_reel` — живая ссылка
- `T + 15 мин` после публикации каждого IG-поста (carousel и reel отдельно)

## Как работает MCP-путь

1. `setup_chatplace_funnel.py` собирает job из Notion
2. `chatplace_mcp.py` → `initialize` → `tools/list` → `tools/call`
3. Prompt описывает воронку: **любой комментарий** (`commentAnyValue`) → DM с `post_url_telegram`
4. Успех → `chatplace_funnel_{carousel|reel}_done=true`, ID в `chatplace_funnel_*_id`

## CLI

```bash
python3 scripts/setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram --post-kind carousel
python3 scripts/setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram --post-kind reel
python3 scripts/setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram  # оба kinds
python3 scripts/setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram --dry-run
python3 scripts/setup_chatplace_funnel.py --page-id PAGE_ID --job-only   # без MCP, только JSON
```

## Cursor MCP

`.cursor/mcp.json`:
```json
"chatplace": {
  "url": "https://mcp.chatplace.io/mcp",
  "headers": { "Authorization": "Bearer ${env:CHATPLACE_API_KEY}" }
}
```

Toggle **chatplace** ON в Customize → MCP.

## Coordinator fallback

Если shell-MCP недоступен — прочитать `data/chatplace_jobs/*.json` и выполнить `mcp_prompt` в чате Cursor с ChatPlace MCP.

## Notion

| Field | Use |
|-------|-----|
| `CTA Instagram` | CTA на посте («Оставьте +…») — не триггер воронки |
| `post_url_telegram` | Ссылка в DM |
| `post_url_instagram_carousel` | Пост carousel для привязки |
| `post_url_instagram_reel` | Пост reel для привязки |
| `chatplace_funnel_carousel_done` / `_reel_done` | Воронка создана |
| `chatplace_funnel_carousel_id` / `_reel_id` | ID в ChatPlace |
| `chatplace_funnel_done` | Все kinds готовы |
