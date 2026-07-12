# Agent 1B — Facebook Marketplace Parser

**Роль:** альтернативный **Agent 1 (Parser)** в мультиагентной системе Real Estate.  
**Не путать с Agent 4 Publisher** — тот публикует *в* Facebook, этот агент *парсит из* Facebook Marketplace.

---

## Место в системе

```
┌──────────────────────────────────────────────────────────────────┐
│              Real Estate Multi-Agent System                      │
├──────────────────────────────────────────────────────────────────┤
│  Agent 1A  Airbnb Parser (есть)     → session → Agent 2          │
│  Agent 1B  FB Marketplace Parser    → session → Agent 2  ← ВЫ   │
│  Agent 2   Strukturator              — Notion + R2 (общий)      │
│  Agent 3   Video Creator             — FFmpeg + Seedance         │
│  Agent 4   Publisher (Publora)       — Instagram/TikTok/FB post  │
│  Agent 5   Qualifier (planned)                                   │
└──────────────────────────────────────────────────────────────────┘
```

**Ваша задача:** собрать объявление с Facebook Marketplace и отдать **в том же контракте**, что Agent 1 (Airbnb), чтобы Agent 2 мог его обработать.

**Родительский проект:** `Desktop/Агенты/Real Estate Agent/agent 4`  
**Код Agent 2:** `_import/assistant-media/`

---

## Что Agent 2 ожидает на входе (контракт)

Agent 2 **не парсит Facebook**. Он читает **локальную session**:

```
data/sessions/{SESSION_ID}/
├── description.txt      # текст объявления (обязательно)
├── photos/
│   ├── photo_001.jpg
│   ├── photo_002.jpg
│   └── ...
├── gate.json            # создаёт media_batch_gate.py
└── session.json         # создаёт Agent 2 после успеха
```

### Минимум для запуска Agent 2

| Требование | Обязательно |
|------------|-------------|
| `description.txt` | ✅ |
| Хотя бы 1 фото в `photos/` | ✅ |
| Уникальный `SESSION_ID` | ✅ |
| `--source` при запуске пайплайна | ✅ (см. ниже) |

### Запуск после вашего парсера

```bash
# 1. Gate (если фото приходят пачками)
python3 scripts/media_batch_gate.py init --session SESSION_ID --description "$(cat description.txt)"
python3 scripts/media_batch_gate.py bump --session SESSION_ID
# ... ждать ready (150 сек после последней пачки)

# 2. Agent 2 + цепочка
./scripts/run_pipeline.sh --session SESSION_ID --source "Facebook Marketplace https://facebook.com/marketplace/item/123456"
```

**`--source` критичен:** попадает в Notion поле **«Источник объявления»** и влияет на `listing_parser.py` (тип аренды).

---

## Формат `description.txt` (рекомендуемый)

Agent 2 парсит **свободный текст** через regex (`listing_parser.py`).  
Чем ближе к структуре Airbnb-объектов, тем лучше заполнятся поля Notion.

### Эталон (как сейчас с Airbnb)

```
TITLE LEGENDARY 2BR с видом на бассейн / БАНГТАО

Choeng Thale, Таиланд

https://www.airbnb.ru/rooms/1628369705787515273

The Title Legendary Bang-Tao — современный курортный комплекс...
• 2 Уютные спальни
• Гостиная с диваном и Smart-TV
...

Важная информация
Возвратный депозит 200 USD
```

**Первая непустая строка** → **Название объекта** в Notion.

### Рекомендуемый шаблон для FB Marketplace

```
{Заголовок объявления FB — первая строка}

{Район}, {Город/Провинция}, Thailand

https://www.facebook.com/marketplace/item/{ITEM_ID}/

{Полное описание с FB — как есть или слегка вычищенное}

Характеристики:
• {N} спален / {N} BR
• {площадь} m²
• Тип: {квартира|вилла|кондо|...}

Цена: {число} THB/месяц
Залог: {число} THB
Тип аренды: долгосрочная

Удобства: бассейн, кондиционер, парковка, Wi-Fi

Контакт продавца: {имя, если есть}
```

### Опционально: JSON рядом (для вашего парсера)

Можете сохранять `data/sessions/{SESSION_ID}/parsed.json` для отладки — **Agent 2 его не читает**.  
Но из JSON собирайте `description.txt` по шаблону выше.

```json
{
  "source_type": "facebook_marketplace",
  "source_url": "https://www.facebook.com/marketplace/item/123456789/",
  "title": "2BR Condo Patong Sea View",
  "location": {
    "district": "Patong",
    "city": "Phuket",
    "country": "Thailand",
    "raw": "Patong, Phuket"
  },
  "price_monthly": 45000,
  "price_yearly": null,
  "currency": "THB",
  "deposit": 90000,
  "rooms": 2,
  "area_sqm": 85,
  "housing_type": "Кондоминиум",
  "rent_type": "long_term",
  "description": "Full text from FB listing...",
  "amenities": ["Pool", "AC", "Parking"],
  "seller_name": "John",
  "photos_local": ["photos/photo_001.jpg"],
  "parsed_at": "2026-07-07T12:00:00Z"
}
```

---

## Как Agent 2 извлекает поля (сейчас — под Airbnb)

Файл: `_import/assistant-media/scripts/listing_parser.py`

| Поле Notion | Как извлекается сейчас | Проблема для FB |
|-------------|------------------------|-----------------|
| **Название** | Первая строка `description.txt` | ОК |
| **Район** | Список `phuket_districts` в `pipeline.json` | FB часто пишет иначе («Patong Beach») — дополнить список или нормализовать в парсере |
| **Комнаты** | Regex: `2 BR`, `2 комнат`, `2 спален` | FB: «2 beds» — **добавить паттерн в парсер или писать «2 спальни» в description** |
| **Площадь** | `85 m²`, `85 sqm` | FB: `sq ft` — **конвертировать в m² в Agent 1B** |
| **Цена/мес** | `45000 THB`, `45000 ฿` | FB: `$500/month`, `45,000 baht` — нормализовать в description |
| **Цена/год** | `... THB ... год/year` | Редко на FB |
| **Залог** | `deposit/депозит/залог: N` | ОК если явно в тексте |
| **Тип жилья** | `housing_type.py` + known_projects | FB categories: Apartment, House — **маппинг в парсере** |
| **Тип аренды** | `airbnb` в source → **Краткосрочная** | FB Marketplace аренда → обычно **Долгосрочная** — **не класть airbnb в source** |
| **Вид** | Regex: sea view, pool view… | Добавлять в описание на EN/RU |
| **Удобства** | Keyword list (бассейн, wifi…) | Дублировать ключевые слова в description |
| **Источник** | URL из `--source` или текст | `--source "Facebook Marketplace {url}"` |
| **Google Maps** | `maps_resolver.py` по адресу+району | FB часто **нет точного адреса** — парсер должен дать максимум локации в тексте |

### Критично для FB

1. **`--source` не должен содержать `airbnb`** — иначе `rent_type` = «Краткосрочная».
2. **Цена в description** — число + `THB`/`฿`/`бат` для regex Agent 2.
3. **Фото** — скачать локально в `photos/photo_NNN.jpg` (Agent 2 заливает в R2 сам).
4. **Ссылка на объявление** — в начале description или в `--source`.

---

## Notion CRM — куда попадают данные (Agent 2)

База: `e817ce50-e788-4992-8b86-c9c9fc1fbcf7`  
Схема: `_import/assistant-media/CRM_SCHEMA.md`

После Agent 2:

| Поле | Пример |
|------|--------|
| Объект ID | `20260707_001` |
| Статус | `ready_for_video` |
| Фото | URL галереи R2 `{object_id}/photos/index.html` |
| Описание для Telegram | генерируется автоматически |
| Описание для FB Marketplace | генерируется автоматически (для *публикации*, не путать с источником) |
| Цена за месяц | число без валюты |

Дальше автоматически: Agent 3 (видео) → `ready_to_post` → Agent 4 (Publora).

---

## Пайплайн gate (если фото идут пачками)

Как в Telegram с Airbnb:

1. `media_batch_gate.py init` — старт session + description
2. После каждой пачки фото → `bump`
3. **150 секунд тишины** после последней пачки → `ready`
4. Только тогда `run_pipeline.sh`

Если FB-парсер отдаёт все фото сразу — можно один `bump` и сразу pipeline с `--skip-wait` (если поддерживается) или дождаться gate.

---

## Что НЕ делает Agent 1B

- Не пишет в Notion (это Agent 2)
- Не создаёт видео (Agent 3)
- Не публикует посты (Agent 4)
- Не дублирует логику `listing_parser.py` — только **подготовка session**

---

## Что придётся доработать в Agent 2 (после FB)

Отдельная задача в родительском проекте:

| # | Доработка |
|---|-----------|
| 1 | `listing_parser.py` — паттерны FB: `beds`, `sq ft`, USD/EUR |
| 2 | `rent_type` — детект `facebook marketplace` → Долгосрочная |
| 3 | `phuket_districts` — расширить синонимы районов |
| 4 | `maps_resolver` — fallback когда только район без адреса |
| 5 | Поле `source_type` в Notion (опционально) — Airbnb vs FB |
| 6 | Дедупликация — один объект с Airbnb и FB (по адресу/фото) |

---

## Интеграционные варианты

### A. Файловая session (проще всего)

FB Parser пишет в общую папку `data/sessions/` на сервере clawbot → запускает `run_pipeline.sh`.

### B. Telegram handoff (как сейчас Airbnb)

Parser-бот шлёт description + фото в TG → clawbot Agent 2 workspace по PLAYBOOK.md.

### C. HTTP webhook

FB Parser POST → endpoint создаёт session → триггер pipeline.

**Рекомендация для v1:** вариант **A** или **B** — без изменения Agent 2.

---

## Соседние архивы / проекты

| Компонент | Где |
|-----------|-----|
| Полный пайплайн | `agent 4/_import/assistant-media/` |
| Agent 4 Publisher | `real-estate-agent4-publisher-20260707.tar.gz` |
| Agent 3 Seedance | `higgsfield-seedance-agent-20260706.tar.gz` |
| Agent 1B FB (этот бриф) | `export/FB_MARKETPLACE_PARSER_BRIEF.md` |

---

## Чеклист готовности FB Parser

- [ ] Стабильный парсинг: title, price, location, description, photos
- [ ] `description.txt` по шаблону выше
- [ ] Фото в `photos/photo_001.jpg` …
- [ ] `source_url` Facebook Marketplace
- [ ] `--source "Facebook Marketplace {url}"` при pipeline
- [ ] Тест: dry-run Agent 2  
      `python3 scripts/agent2_structurize.py --session TEST_FB --source "Facebook Marketplace https://..." --dry-run`
- [ ] Сверка полей в Notion с эталонным Airbnb-объектом

---

## Для AI в новом чате

> «Ты **Agent 1B — Facebook Marketplace Parser**, часть Real Estate Multi-Agent.  
> Читай этот файл. Твой output — session folder для **Agent 2 Strukturator**.  
> Не трогай Notion/R2/видео. Контракт — `description.txt` + `photos/`.»

---

_Дата: 2026-07-07. Источник: agent 4/_import/assistant-media/_
