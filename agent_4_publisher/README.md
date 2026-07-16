# Real Estate — Agent 6 Publisher (Metricool)

**Agent 6** из мультиагентной системы недвижимости.  
Публикует готовые объекты: **Notion CRM → [Metricool](https://app.metricool.com/) → Instagram / TikTok / Threads / …**

> Контекст всей системы: **`MULTI_AGENT.md`**

---

## Быстрый старт

```bash
cp .env.example .env
```

### 1. Notion
Заполни в `.env`: `NOTION_API_KEY`, `NOTION_DB_ID`

### 2. Metricool API
1. Зайди на https://app.metricool.com/ → **Account Settings → API** → скопируй `userToken`
2. Открой нужный бренд в дашборде — из URL возьми `userId` и `blogId`
3. Заполни `.env`:
   ```
   METRICOOL_USER_TOKEN=...
   METRICOOL_USER_ID=...
   METRICOOL_BLOG_ID=...
   METRICOOL_TIMEZONE=Asia/Bangkok
   ```
4. Проверь подключённые соцсети:
   ```bash
   python3 scripts/list_brands.py
   ```

> API доступен на плане **Advanced** и выше.

### 2b. MCP в Cursor (уже установлено)

Файл `/.cursor/mcp.json` в корне workspace — серверы **metricool** и **chatplace**.  
После заполнения `METRICOOL_*` в `.env` → **перезагрузите Cursor** (Reload Window).  
Подробнее: `docs/METRICOOL_MCP.md`

### 3. Telegram
Токен и канал в `.env` (не в `.env.example`):
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL=@OpenHome_th
```
```bash
python3 scripts/publish_telegram.py --check-bot
python3 scripts/publish_telegram.py --page-id PAGE_ID --dry-run
python3 scripts/publish_telegram.py --page-id PAGE_ID
```

### 4. Metricool / публикация в соцсети
```bash
python3 scripts/publish_pipeline.py --page-id PAGE_ID --platform instagram --dry-run
python3 scripts/publish_pipeline.py --page-id PAGE_ID --platform instagram
python3 scripts/publish_pipeline.py --queue --platform instagram
```

Файл `.env` лежит в корне `real-estate-agent6-publisher/`.

---

## Зависимости

Только Python 3.10+ stdlib — без pip install.

---

## Структура

```
real-estate-agent6-publisher/
├── MULTI_AGENT.md      ← роль в системе (читай первым!)
├── AGENTS.md           ← инструкции для AI-агента
├── CHAIN.md            ← триггеры Notion
├── config/publisher.json
├── scripts/
│   ├── publish_pipeline.py
│   └── list_brands.py
└── skills/
    ├── publish-metricool/
    └── notion-crm/
```

---

## Metricool flow

1. `GET /actions/normalize/image/url` — скопировать видео/фото на CDN Metricool
2. `POST /v2/scheduler/posts` — запланировать пост с `providers`, `media`, `publicationDate`

Docs: https://app.metricool.com/resources/apidocs/index.html

---

## Видео и карусель

| Что | Откуда в Notion |
|-----|-----------------|
| **Видео** (все соцсети) | `video_url_Seedance` (fallback: `video_url_vertical`) |
| **Карусель фото** | `Фото` → R2 gallery (`index.html` или прямая ссылка) |

Карусель включается для: **Instagram, TikTok, Facebook, X (Twitter), Threads, LinkedIn**  
(настройка: `config/publisher.json` → `carousel.platforms`)

Для Instagram / Threads / X / LinkedIn при наличии карусели — **только фото** (без видео в том же посте).  
TikTok / Facebook — видео + карусель вместе.

TikTok: `tiktokData.autoAddMusic: true` в `config/publisher.json`.

После публикации ссылки записываются в Notion: `post_url_instagram_carousel`, `post_url_instagram_reel`, `post_url_tiktok`, ...

**Два этапа URL (Metricool):**
1. Сразу после планирования — ссылка на пост в календаре Metricool (`Copy link` в planner).
2. Через 5 минут после времени публикации — перезапись на реальную ссылку соцсети.

Логи отложенной синхронизации: `data/deferred_sync/`.  
Вручную: `python3 scripts/sync_post_urls.py --page-id PAGE_ID --platform linkedin`  
(`--post-id` не обязателен — пост ищется по `Объект ID` в тексте Metricool).

---

## Статус

Код переписан под Metricool.  
Нужно заполнить `METRICOOL_*` в `.env` и прогнать dry-run на тестовой записи.

Источник: `agent 4/workspaces/publisher/` → мигрирован в `agent_6_piblich_social/`  
Дата архива: 2026-07-07
