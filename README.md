# Real Estate Agent — мультиагентная система аренды недвижимости

Пайплайн: парсинг (FB/Airbnb) → структуризация в Notion CRM + R2 → видео → публикация → воронки → квалификация лидов → связь с собственником → документ бронирования → договор аренды.

Источник истины по архитектуре — страница Notion «Общая структура системы» и её дочерние страницы (Агент_1…Агент_8).
Временная карта ролей и legacy-имён при рефакторинге: [`ROLE_MAP.md`](ROLE_MAP.md).

## Агенты

| Папка | Агент (Notion) | Роль | Статус |
|---|---|---|---|
| `agent_1_parser/fb_parser` | Агент_1 Parser | Парсинг FB Marketplace через Telegram-бот | Работает |
| `agent_1_parser/airbnb_scraper` | Агент_1 Parser | Парсинг Airbnb (отдельный трек, миграция отдельной задачей) | Работает |
| `agent_2_registrar` | Агент_2 Registrar | Структуризация в Notion + загрузка фото на R2, оркестрация цепочки (бывший монорепо «agent 4», внутри также legacy-код Агента 3 и старого publisher) | Работает |
| `agent_3_director` | Агент_3 Director | Генерация видео Seedance 2.0 (Higgsfield), хук-оверлей, музыка, загрузка на R2 | Работает |
| `agent_4_publisher` | Агент_4 Publisher | Telegram + Metricool (выкл.) + ChatPlace; соцсети через телефон | Работает |
| `agent_4_publisher_social` | Агент_4 / phone | Публикация в TikTok/IG/YT/LI/X/FB с Android (ADB) | Работает |
| `agent_5_usher` | Агент_5 Usher | Ждёт выход отложенных постов, фиксирует ссылки, настраивает ChatPlace-воронки | Код пока живёт в `agent_4_publisher/scripts` (chatplace_*) |
| `agent_6_qualifier` | Агент_6 Qualifier | Квалификация лидов (TG userbot + Gemini), amoCRM | В работе |
| `agent_7_envoy` | Агент_7 Envoy | Связь с собственником (WA → TG → Airbnb → FB) | Код пока живёт в `agent_6_qualifier/src/agent8` |
| `agent_8_notary` | Агент_8 Notary | Документ брони (docx) + amoCRM; договор аренды — в планах | Код в `agent_6_qualifier/src/agent8/booking_doc.py` |

## Общие ресурсы

- **Notion CRM** — единая таблица объектов (`NOTION_DB_ID`), контракт колонок: `schema/notion_schema.json`, валидатор: `schema/validate_schema.py`
- **Cloudflare R2** — фото, видео, музыка
- **Metricool** — отложенный постинг в соц.сети
- **amoCRM** — сделки (Qualifier/Envoy/Notary)

## Формат Объект ID

`F_YYYYMMDD_NNN` — источник Facebook, `A_YYYYMMDD_NNN` — источник Airbnb.

## Секреты

Каждый агент держит свой `.env` (в git не попадает). Корневой `.env` — ключ Notion для схема-валидатора и общих скриптов.

## План деплоя (договорённость)

После завершения сквозных тестов проект заливается на сервер как есть (монорепа), запуск — по сервису на агента через docker-compose (или systemd): постоянные сервисы — Агент 1 (TG-бот), Агент 6 (квалификатор), поллер Агента 4; остальные вызываются по цепочке. Общий `.env` монтируется всем сервисам, упавший агент перезапускается автоматически.
