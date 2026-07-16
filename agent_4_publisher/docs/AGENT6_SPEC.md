# Agent 6 — Спецификация (Publisher)

> Версия: 2026-07-07  
> Статус: проектирование → поэтапная реализация

## Роль агента

**Agent 6** — дистрибьютор контента объекта недвижимости из Notion CRM в соцсети через Metricool, с обязательной предварительной публикацией в Telegram-канале и подключением воронки ChatPlace для Instagram и TikTok.

Agent 6 **не** парсит, **не** монтирует видео, **не** квалифицирует лиды.

---

## Источники данных (Notion CRM)

| Колонка | Назначение |
|---------|------------|
| **Фото** (col ~10) | URL галереи R2 (`index.html` или прямые `.jpg`) → карусель |
| **Описание сец.сети** (col ~13) | Текст для Metricool (Instagram, TikTok, X, LinkedIn, FB) |
| **Описание для Telegram** (col ~3) | Текст для TG-канала |
| **video_url_Seedance** (col ~33) | URL готового видео 9:16 с R2 → Reels / TikTok video |
| **Объект ID** | Идемпотентность, хэштег, связь с ChatPlace |
| **Статус** | Конвейер и блокировка повторов |

---

## Три функции

### F1 — Карусель (отложенно, Metricool)

**Когда:** объект в очереди, карусель ещё не планировалась, дневной лимит не исчерпан.

**Контент:** из `Фото` → парсинг R2 gallery → до 10 изображений.

**Платформы:** Instagram, TikTok, X.com, LinkedIn, Facebook.

**Тип поста в Metricool:**
- Instagram — POST (карусель, без видео в том же посте)
- TikTok — фото-карусель + `autoAddMusic: true`
- X / LinkedIn / Facebook — карусель изображений

**Не включает:** YouTube, Threads (пока не в ТЗ).

---

### F2 — Видео Reel (отложенно, Metricool)

**Когда:** `video_url_Seedance` заполнен, видео ещё не планировалось, лимит не исчерпан.

**Контент:** `video_url_Seedance` с R2.

**Платформы:** Instagram (REEL), TikTok, X, LinkedIn, Facebook — по той же логике, что и карусель, но с видео.

---

### F3 — Публикация по команде (срочно)

**Триггер:** оператор передаёт `page_id` (Notion) + опционально `--mode carousel|video|all`.

**Поведение:**
- Игнорирует дневной лимит (или отдельный «форс»-слот)
- Может снять блокировку `agent6_locked` при флаге `--force`
- Выполняет полный цикл: TG → Metricool → ChatPlace (если IG/TikTok)

```bash
python3 scripts/publish_pipeline.py --page-id PAGE_ID --mode carousel --force
python3 scripts/publish_pipeline.py --page-id PAGE_ID --mode video --force
python3 scripts/publish_pipeline.py --page-id PAGE_ID --mode all --force
```

---

## Обязательный порядок: Telegram → Metricool → ChatPlace

```
┌─────────────────────────────────────────────────────────────────┐
│ 0. ПРОВЕРКИ: lock, лимит 3/сеть, интервал 3–4 ч, R2 доступны     │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. TELEGRAM @OpenHome_th (сразу, не отложенно)             │
│    • Только ИЗОБРАЖЕНИЯ из Фото (R2 gallery)                    │
│    • Seedance в TG НЕ публикуем                                   │
│    • Текст: Описание для Telegram (готовое от Agent 2)          │
│    • → post_url_telegram                                          │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. METRICOOL (отложенно, слоты 10:00 / 14:00 / 18:00 ICT)       │
│    • F1 карусель и/или F2 видео                                 │
│    • → post_url_instagram, post_url_tiktok, ...                 │
│    • → metricool_post_id (лучше: JSON по платформам)             │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. CHATPLACE (IG + TikTok)                                      │
│    • Триггер: комментарий/DM по ключевому слову (#obj_ID)       │
│    • Действие: выдать ссылку post_url_telegram                  │
│    • Привязка к конкретному посту IG/TikTok                     │
└─────────────────────────────────────────────────────────────────┘
```

**Правило:** ChatPlace **нельзя** настраивать до `post_url_telegram`.  
Для привязки к IG/TikTok — ждать `post_url_*` (или `sync_post_urls.py` после выхода поста).

---

## Лимит: не более 3 постов **в одну соцсеть** за сутки

Лимит считается **отдельно для каждой сети** (Instagram, TikTok, X, LinkedIn, Facebook), не «3 объекта».

### Сети с каруселью и видео (все 5 из ТЗ)

| Тип | Макс. в сутки на сеть |
|-----|------------------------|
| Карусель (F1) | **1** |
| Видео Seedance (F2) | **2** |
| **Итого** | **3 поста** |

### Сети только с видео (если добавим позже, напр. YouTube)

| Тип | Макс. в сутки |
|-----|----------------|
| Видео | **3** |
| Карусель | 0 |

### Интервал между публикациями

- Минимум **3–4 часа** между любыми постами **в одну сеть** (карусель → видео → видео).
- Пример слотов (Asia/Bangkok): **10:00** карусель, **14:00** видео #1, **18:00** видео #2.

**Хранение счётчика:** `data/daily_quota/{network}/YYYY-MM-DD.json` или Notion + запросы к Metricool за сегодня.

| Считается | Не считается |
|-----------|--------------|
| Каждый запланированный пост в сеть | F3 `--force` (опционально, настраивается) |
| Повтор того же page_id в ту же сеть | `sync_post_urls` |

---

## Блокировка повторов (idempotency)

### Колонки Notion (предлагаемые)

| Поле | Тип | Назначение |
|------|-----|------------|
| `agent6_locked` | Checkbox | Объект взят Agent 6, авто-повтор запрещён |
| `agent6_carousel_done` | Checkbox | Карусель запланирована |
| `agent6_video_done` | Checkbox | Видео запланировано |
| `agent6_taken_at` | Date | Когда взяли в работу |
| `agent6_mode` | Select | `auto` / `manual` / `force` |

**Алгоритм:**
1. Перед работой: если `agent6_locked` и не `--force` → SKIP
2. Сразу после взятия: `agent6_locked = true`, `agent6_taken_at = now`
3. После успеха F1: `agent6_carousel_done = true`
4. После успеха F2: `agent6_video_done = true`
5. Сброс только вручную: снять `agent6_locked` в Notion или `--force --reset-lock`

---

## Триггер от Agent 5 (позже)

```http
POST /hooks/agent6
{ "object_id": "20260701_001", "trigger": "seedance_ready" }
```

Условия:
- `video_url_Seedance` заполнен
- `agent6_locked` = false
- дневной лимит OK

→ запуск F2 (видео) в авто-режиме.  
Реализация после E2E тестов F1/F2/F3.

---

## ChatPlace.io

- API / MCP: `https://mcp.chatplace.io/mcp` + API key из настроек ChatPlace
- Для каждого объекта: automation с keyword = `#OBJ_xxx` или `Описание` snippet
- Воронка: Comment/DM trigger → сообщение со ссылкой `post_url_telegram`
- Отдельный skill: `skills/chatplace-funnel/SKILL.md`
- Env: `CHATPLACE_API_KEY`, шаблон воронки `CHATPLACE_FUNNEL_TEMPLATE_ID`

**Риск:** публичный REST API ChatPlace слабо документирован — возможен MVP через MCP или ручной шаблон + переменная `{tg_link}`.

---

## Telegram (мгновенная публикация)

**Канал:** [@OpenHome_th](https://t.me/OpenHome_th)

**Контент:** только фото объекта из колонки **Фото** (R2 gallery, `sendMediaGroup`).  
**Не публикуем:** `video_url_Seedance`, FFmpeg reel и любое видео.

### Нужен ли бот?

**Да.** Автопостинг в канал — только через [Telegram Bot API](https://core.telegram.org/bots/api):

1. Создать бота у [@BotFather](https://t.me/BotFather) → получить `TELEGRAM_BOT_TOKEN`
2. Добавить бота в канал **@OpenHome_th** как **администратора**
3. Выдать право **«Публикация сообщений»** (Post messages)
4. В `.env`:
   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHANNEL=@OpenHome_th
   ```

Скрипт: `scripts/publish_telegram.py`  
После `sendMediaGroup` → `post_url_telegram` = `https://t.me/OpenHome_th/{message_id}`

---

## Улучшения (что легко упустить)

### Критичные

1. **Разделить F1 и F2** — один объект может получить и карусель, и видео; нужны отдельные флаги `agent6_carousel_done` / `agent6_video_done`, иначе второй тип никогда не уйдёт.
2. **Лимит 3/сеть** — 1 карусель + 2 видео; интервал 3–4 ч; счётчик per-network.
3. **Колонка Фото** — в config: `Фото` (без пробела).
4. **Два текста** — TG: «Описание для Telegram»; Metricool: только «Описание сец.сети». Колонка «Описание» не используется для публикации.
5. **ChatPlace после live** — IG/TikTok post ID нужен для привязки automation; планировать `sync_post_urls` + отложенный шаг ChatPlace (cron через 15–60 мин).
6. **Частичный сбой** — TG ок, Metricool fail → не снимать lock полностью; писать `last_error` + `agent6_partial_done`.

### Операционные

7. **Валидация R2** — HEAD-запрос к `Фото` и `video_url_Seedance` до публикации.
8. **Длина текста** — X 280, LinkedIn 3000; обрезка с логом в `last_error`.
9. **Служебный алёрт** — TG-чат при ошибке Agent 6.
10. **Audit log** — колонка `agent6_log` (rich text) или JSON: что, когда, post_id.
11. **metricool_post_ids** — JSON `{"instagram":"123","tiktok":"456"}` вместо одного поля.
12. **Stagger** — разнести F1 и F2 на разные слоты дня, если оба в один день.
13. **Dry-run** — `--dry-run` для всего цепочки без lock и без API write.

### Продуктовые

14. **A/B время** — Metricool best times API для слотов.
15. **YouTube / Threads** — вынести в phase 2, не смешивать с MVP.
16. **Facebook Marketplace** — отдельный flow (не Metricool scheduler).

---

## Фазы реализации

| Фаза | Что | Статус |
|------|-----|--------|
| 0 | Спека + config + колонки Notion | ✅ этот документ |
| 1 | Caption «Описание», карусель 5 сетей, lock + quota | 🔜 |
| 2 | `publish_telegram.py` + порядок TG first | 🔜 |
| 3 | F2 video-only pipeline, отдельные флаги | 🔜 |
| 4 | F3 `--force` / `--mode` | 🔜 |
| 5 | `sync_post_urls` + ChatPlace funnel | 🔜 |
| 6 | Webhook от Agent 5 | после тестов |

---

## CLI (целевой интерфейс)

```bash
# Авто-очередь (cron 09:00 ICT)
python3 scripts/agent6_run.py --auto --max-per-day 3

# Карусель одного объекта
python3 scripts/agent6_run.py --page-id ID --mode carousel

# Видео по команде
python3 scripts/agent6_run.py --page-id ID --mode video --force

# Только TG
python3 scripts/publish_telegram.py --page-id ID

# Догрузка ссылок + ChatPlace
python3 scripts/sync_post_urls.py --page-id ID --setup-chatplace
```

---

_Для AI: читай `AGENT6_SPEC.md` → `AGENTS.md` → `config/publisher.json`._
