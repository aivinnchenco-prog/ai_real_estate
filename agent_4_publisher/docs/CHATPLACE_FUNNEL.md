# ChatPlace — воронка через MCP

> Основной путь: **ChatPlace MCP** (`https://mcp.chatplace.io/mcp`).  
> REST API не используем.

## Цепочка

```
TG (post_url_telegram)
  → Metricool IG carousel + reel (+ CTA Instagram)
  → T+5 мин: sync post_url_instagram_carousel / post_url_instagram_reel
  → T+15 мин: setup_chatplace_funnel.py (отдельно на carousel и reel) → ChatPlace MCP
  → Notion: chatplace_funnel_*_id + chatplace_funnel_*_done
```

## MCP в проекте

| Компонент | Роль |
|-----------|------|
| `scripts/chatplace_mcp.py` | JSON-RPC клиент MCP |
| `scripts/setup_chatplace_funnel.py` | Готовит prompt + вызывает MCP |
| `scripts/deferred_chatplace_funnel.py` | Отложенный запуск после публикации |
| `.cursor/mcp.json` | Сервер `chatplace` для Cursor |
| `skills/chatplace-funnel/SKILL.md` | Правила для coordinator |

## Включение

1. ChatPlace → API settings → создать ключ
2. `.env`: `CHATPLACE_API_KEY=...`
3. Cursor → Customize → MCP → **chatplace** ON
4. `chatplace.enabled: true` в `config/publisher.json`

## Ручной запуск

```bash
# Проверка prompt без MCP
python3 scripts/setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram --dry-run

# Создать воронку через MCP
python3 scripts/setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram --post-kind carousel
python3 scripts/setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram --post-kind reel
# или оба сразу (все kinds из config):
python3 scripts/setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram
```

`setup_chatplace_funnel.py`:
1. Собирает данные из Notion (TG URL, IG URL, object_id, CTA)
2. Формирует `mcp_prompt` на английском (ChatPlace MCP лучше понимает)
3. `tools/list` → выбирает tool для automation/funnel
4. `tools/call` с prompt
5. Пишет результат в Notion

Fallback: `--job-only` — только JSON в `data/chatplace_jobs/` (для ручного MCP в чате Cursor).

## Аккаунты

| Платформа | Аккаунт |
|-----------|---------|
| Instagram (Metricool + ChatPlace) | **@workdvorak** |
| TikTok (бренд) | **@vinchencso_08** |

## Триггер воронки

**Любой комментарий** под постом (`commentAnyValue`). CTA на посте («Оставьте +…») — только призыв в тексте, не фильтр в ChatPlace.

Config: `chatplace.instagram_comment_trigger: "commentAnyValue"`  
Имя воронки: `chatplace.funnel_name_template` → по умолчанию `{object_id}` (колонка **Объект ID** в CRM).

## Prompt (пример)

```
Create an Instagram comment-to-DM automation in ChatPlace:
- Bind to post: https://www.instagram.com/p/...
- Trigger: any comment (commentAnyValue)
- DM message: Полная информация об объекте 20260702_001: https://t.me/...
```

## Cursor coordinator

Если Python MCP из shell блокируется (Cloudflare) — coordinator в Cursor с включённым MCP `chatplace`:

1. Прочитать `data/chatplace_jobs/{page_id}_instagram.json`
2. Выполнить `mcp_prompt` через ChatPlace MCP в чате
3. Записать `chatplace_funnel_id` в Notion

## Notion

| Поле | Назначение |
|------|------------|
| CTA Instagram | Текст на IG-посте |
| post_url_telegram | Ссылка в DM |
| post_url_instagram_carousel | Привязка воронки carousel |
| post_url_instagram_reel | Привязка воронки reel |
| chatplace_funnel_carousel_done / _reel_done | Готово по типу поста |
| chatplace_funnel_carousel_id / _reel_id | ID automation в ChatPlace |
| chatplace_funnel_done | Все kinds из config готовы |
