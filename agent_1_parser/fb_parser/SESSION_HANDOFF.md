# Agent 1B — Session Handoff (2026-07-08)

Сохранено перед перезагрузкой ПК. После ребута открыть этот файл / новый чат в этой папке.

## Роль

- **Agent 1B** — Facebook Marketplace Parser (Real Estate Multi-Agent)
- Output для Agent 2: `data/sessions/{SESSION_ID}/description.txt` + `photos/`
- **Не трогаем** Notion / R2 / видео / Agent 2 (доработки Agent 2 — в другом чате)
- Brief: `FB_MARKETPLACE_PARSER_BRIEF.md`

## Путь проекта

```
/Users/lifefmg/Desktop/Агенты/Real Estate Agent/Agent 1_1 FB Parser
```

## Стек

- Python **3.11** venv: `.venv311` (не 3.14 — crawl4ai/lxml там ломается)
- Backends: `crawl4ai` (основной), `scrapegraphai` (опционально)
- TG бот: `aiogram` → `agent1b/tg_bot.py`
- Сессия FB: Playwright persistent profile `.fb_profile`
- Бриф-контракт: `description.txt` + `photos/photo_NNN.jpg`

## Ключевые файлы

| Файл | Назначение |
|------|------------|
| `agent1b/fb_parser.py` | Парсер FB item → session |
| `agent1b/tg_bot.py` | TG вход: ссылка → парсер → text+photos в чат |
| `agent1b/fb_session.py` | Логин, прокси, delays, profile lock |
| `agent1b/login_fb.py` | Однократный логин + сохранение cookies |
| `.env` | Secrets (не коммитить) |
| `.env.example` | Шаблон env |

## .env — правильный формат

```bash
TELEGRAM_BOT_TOKEN=...
FB_PARSER_BACKEND=crawl4ai
FB_PARSER_WORKSPACE=.
FB_EMAIL=...
FB_PASSWORD=...
FB_BROWSER_PROFILE=.fb_profile   # ТОЛЬКО путь к папке, НЕ email/password
FB_HEADLESS=false                # для login_fb.py (видимый браузер)
FB_HEADLESS_PARSER=true          # для TG-бота (headless)
FB_PROXY=                        # optional http://user:pass@host:port
FB_DELAY_MIN_MS=800
FB_DELAY_MAX_MS=2200
FB_PAGE_DELAY_SEC=4
```

**Баг, который уже ловили:** пользователь вписал email/password в `FB_BROWSER_PROFILE` → браузер падал. Поля раздельные.

## После ребута — запуск

```bash
cd "/Users/lifefmg/Desktop/Агенты/Real Estate Agent/Agent 1_1 FB Parser"
source .venv311/bin/activate
# Если сессия FB слетела / нет cookies c_user:
python agent1b/login_fb.py
# Затем бот:
python agent1b/tg_bot.py
```

В Telegram боту отправить URL вида:
`https://www.facebook.com/marketplace/item/{ID}/`

## Что уже работает / чинили

1. crawl4ai ставится только под Python 3.11 (`.venv311`).
2. Нельзя брать все `media.images` страницы — туда попадают related listings. Нужны фото карточки item (+ фильтр превью `261x260`).
3. Описание раньше попадало как markdown ленты (категории TH) — починили: JS sidebar + HTML JSON fields + `is_marketplace_noise` + TG шлёт `parsed.json` поля, не сырой dump.
4. FB блокирует headless без сессии → `AUTH_REQUIRED` / `WRONG_PAGE` / anti-bot. Нужен `login_fb.py` + `.fb_profile`.
5. `UnicodeEncodeError` при записи `description.txt` (эмодзи/surrogates) — добавлены `sanitize_text` / `safe_write_text`.
6. Бот после успеха шлёт: статус + полное описание текстом + альбомы фото (по 10).
7. Два браузера на один profile ломали Playwright — `profile_lock` + логин отдельно от парсера.

## Последние сессии (для отладки)

- `FB_20260708_180359` — 10 фото собраны, description был пустой из-за Unicode crash (уже исправлено)
- `FB_20260708_180640` — проверить после ребута
- Папка `karenlopez1213372@gmail.com/` — артефакт ошибочного `FB_BROWSER_PROFILE`, можно удалить позже

## Коды ошибок парсера

| Code | Значение |
|------|----------|
| 0 | OK |
| 1 | PARSER_FAILED / прочее |
| 2 | AUTH_REQUIRED |
| 3 | NO_PHOTOS |
| 4 | WRONG_PAGE (лента вместо карточки) |

## Следующие шаги после возврата

1. Запустить `login_fb.py` если нужно.
2. Запустить `tg_bot.py`.
3. Прогнать тестовую ссылку item — дождаться полного description + photos в TG.
4. Если anti-bot снова — настроить `FB_PROXY`, держать `FB_HEADLESS_PARSER=true` после успешного login.
5. Agent 2 handoff — вне этого чата (по брифу).

## Безопасность

- Токен TG и FB password уже светились в сессии — после стабилизации: revoke BotFather token + сменить FB password.
- Не коммитить `.env`, `.fb_profile`.
