# Agent 6 Publisher — состояние сессии

> **Обновлено:** 2026-07-08 (UTC+7)  
> **Продолжить с:** тестовый объект `20260702_001`, воронка reel, включение автопайплайна ChatPlace

---

## Цель проекта

**Notion CRM → Telegram (сразу) → Metricool (отложенно) → ChatPlace (IG воронка) → URLs обратно в Notion**

Путь к проекту:
`/Users/lifefmg/Desktop/Агенты/Real Estate Agent/agent_6_piblich_social/real-estate-agent6-publisher/`

---

## Аккаунты

| Платформа | Аккаунт | Где |
|-----------|---------|-----|
| Instagram | **@workdvorak** | Metricool + ChatPlace |
| TikTok (бренд) | **@vinchencso_08** | Metricool (воронка TikTok — phase 5.1, не сделано) |
| Telegram | **@trip_home_phuket** | Прямая публикация |

---

## Тестовый объект

| Поле | Значение |
|------|----------|
| Объект ID | `20260702_001` |
| Notion page_id | `3912c251-5061-81fa-974d-eaf233f581ba` |
| Объект | LEGENDARY 2BR, Choeng Thale |
| TG пост | `https://t.me/trip_home_phuket/12` |
| IG carousel (live) | `https://www.instagram.com/p/DagA9t2iK36/` |

---

## ChatPlace — что сделано в этой сессии

### Настройки (`config/publisher.json` → `chatplace`)

```json
{
  "enabled": false,
  "execution": "mcp",
  "use_structured_mcp": true,
  "delay_minutes_after_publish": 15,
  "platforms": ["instagram"],
  "instagram_post_kinds": ["carousel", "reel"],
  "instagram_comment_trigger": "commentAnyValue",
  "trigger_keyword": "+",
  "funnel_name_template": "{object_id}",
  "dm_message_template": "Полная информация об объекте {object_id}: {telegram_url}"
}
```

### Поведение воронки

1. **Триггер:** любой комментарий (`commentAnyValue`), не фильтр по `+`
2. **CTA на посте:** «Оставьте +…» — только текст в IG, колонка **CTA Instagram**
3. **Две воронки на объект:** отдельно **carousel** и **reel**
4. **Имя воронки:** `Объект ID` из CRM (напр. `20260702_001`) через `automations_update`
5. **DM:** ссылка `post_url_telegram`
6. **Задержка:** T+15 мин после публикации IG (после T+5 мин sync URL)

### Ключевые скрипты

| Скрипт | Роль |
|--------|------|
| `scripts/chatplace_mcp.py` | MCP JSON-RPC, `create_instagram_comment_funnel`, rename |
| `scripts/setup_chatplace_funnel.py` | Job из Notion, MCP, запись в CRM |
| `scripts/deferred_chatplace_funnel.py` | Отложенный запуск из pipeline |
| `scripts/publish_pipeline.py` | Спавнит deferred URL sync + ChatPlace |

### Notion — колонки ChatPlace

| Колонка | Назначение |
|---------|------------|
| `CTA Instagram` | Текст CTA на IG-посте |
| `chatplace_funnel_carousel_done` | Воронка carousel готова |
| `chatplace_funnel_reel_done` | Воронка reel готова |
| `chatplace_funnel_carousel_id` | ID automation carousel |
| `chatplace_funnel_reel_id` | ID automation reel |
| `chatplace_funnel_done` | Оба kinds готовы |
| `chatplace_funnel_id` | Legacy (carousel ID) |
| `post_url_instagram_carousel` | URL carousel |
| `post_url_instagram_reel` | URL reel |
| `post_url_telegram` | Ссылка в DM |

Колонки `chatplace_funnel_*_done/id` добавлены в CRM 2026-07-08.

### ChatPlace IDs (тест)

| Сущность | ID |
|----------|-----|
| Instagram bot (@workdvorak) | `019f3cc7-9526-720d-b985-53e6798a0282` |
| Media (carousel post) | `019f3dc7-e98f-736e-bc81-682f34111920` |
| Automation carousel | `019f3dc8-a314-7150-9a84-cd235ed3594d` |
| Имя automation | `20260702_001` (переименовано) |
| Триггер | `commentAnyValue` (обновлено) |
| Статус | Active |

### Job-файлы

`data/chatplace_jobs/{page_id}_instagram_{carousel|reel}.json`

---

## Metricool — тестовый объект `20260702_001`

| Платформа | Post ID | Статус |
|-----------|---------|--------|
| IG carousel | 346596381 | ✅ Published, URL synced |
| IG reel | 346596437 | ⏳ Pending (planner URL в CRM) |
| TikTok | 346598461 | ⏳ Pending |
| Facebook | 346598568 | ⏳ Pending |
| LinkedIn | 346617307 | ✅ Manually published |
| X | 346598732 | ⏳ Pending |
| Threads | 346598823 | ⏳ Pending |

**URL sync:** T+5 мин после `publicationDate` (`metricool.url_sync_delay_minutes: 5`).  
Поиск постов по `Объект ID` в caption — `scripts/metricool_post_search.py`.

---

## Env / MCP

- `.env`: `CHATPLACE_API_KEY`, `METRICOOL_*`, `NOTION_*`, `TELEGRAM_*` — настроены
- `.cursor/mcp.json`: сервер `chatplace` → `https://mcp.chatplace.io/mcp`
- MCP из shell работает (93 tools)

---

## CLI — полезные команды

```bash
cd real-estate-agent6-publisher

# Sync URL вручную
python3 scripts/sync_post_urls.py --page-id 3912c251-5061-81fa-974d-eaf233f581ba --platform instagram --post-kind carousel

# ChatPlace — carousel (уже сделано, skip)
python3 scripts/setup_chatplace_funnel.py --page-id 3912c251-5061-81fa-974d-eaf233f581ba --platform instagram --post-kind carousel --dry-run

# ChatPlace — reel (когда live URL)
python3 scripts/setup_chatplace_funnel.py --page-id 3912c251-5061-81fa-974d-eaf233f581ba --platform instagram --post-kind reel

# Оба kinds сразу
python3 scripts/setup_chatplace_funnel.py --page-id PAGE_ID --platform instagram
```

---

## Следующие шаги (завтра)

1. **Дождаться публикации IG reel** → auto sync `post_url_instagram_reel` (live `instagram.com/...`)
2. **Создать воронку reel** для `20260702_001` (имя: `20260702_001`, триггер `commentAnyValue`)
3. **Включить автопайплайн:** `chatplace.enabled: true` после E2E проверки carousel + reel
4. **Опционально:** различать имена carousel/reel в ChatPlace — `funnel_name_template: "{object_id} {post_kind}"`
5. **Phase 5.1:** TikTok воронка после публикации TikTok (`@vinchencso_08`)
6. **metricool_post_ids JSON** per platform — сейчас один `metricool_post_id` перезаписывается

---

## Документация

- `docs/CHATPLACE_FUNNEL.md` — полная спека ChatPlace
- `skills/chatplace-funnel/SKILL.md` — skill для coordinator
- `docs/AGENT6_SPEC.md` — общая спека Agent 6
- `AGENTS.md` — краткий контекст агента

---

## Решения пользователя (зафиксировано)

- ChatPlace и Metricool IG = **@workdvorak**
- **@vinchencso_08** = бренд TikTok, не IG
- Триггер воронки = **любой комментарий**, не keyword `+`
- Воронки на **carousel и reel** отдельно
- Имя воронки = **Объект ID** из CRM
