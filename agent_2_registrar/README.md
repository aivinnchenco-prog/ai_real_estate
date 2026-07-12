# Real Estate Multi-Agent System (OpenClaw)

Мультиагентная система для недвижимости: парсинг объявлений → CRM → видео → публикация в соцсети.

## Архитектура

```
                    ┌─────────────────┐
  URL / задача  ──► │  coordinator    │  ← главная точка входа
                    └────────┬────────┘
                             │ sessions_spawn (pipeline)
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
   ┌──────────┐        ┌──────────┐        ┌──────────┐
   │  parser  │   ──►  │   crm    │   ──►  │  video   │
   └──────────┘        └──────────┘        └────┬─────┘
                                                 │
                                                 ▼
                                          ┌─────────────┐
                                          │  publisher  │
                                          └──────┬──────┘
                    ┌────────────────────────────┼────────────────────────────┐
                    ▼                            ▼                            ▼
            publish-metricool          publish-fb-marketplace        publish-fb-groups
         (IG, TikTok, YouTube)           (Facebook Marketplace)         (Facebook Groups)
```

### Агенты

| ID | Роль | Что делает |
|----|------|------------|
| `coordinator` | Оркестратор | Принимает задачи, запускает пайплайн, отчитывается |
| `parser` | Парсер | Собирает описание, цену, характеристики, фото |
| `crm` | CRM | Структурирует данные в Noushen CRM |
| `video` | Видео | Создаёт ролик из фотографий объекта |
| `publisher` | Публикация | Публикует через Metricool / Facebook |

### Обмен данными

Все агенты пишут и читают файлы в `data/` по единому контракту:

```
data/
├── listings/{id}/raw.json      ← parser
├── listings/{id}/photos/       ← parser
├── crm/{id}.json               ← crm
├── media/{id}/video.mp4        ← video
└── publish-queue/{id}.json     ← publisher (готов к публикации)
```

## Быстрый старт

### 1. Скопировать проект на сервер с OpenClaw

```bash
# Распаковать/склонировать в удобное место, например:
cp -r . ~/real-estate-agent
cd ~/real-estate-agent
```

### 2. Настроить конфиг OpenClaw

```bash
cp deploy/openclaw.json.example ~/.openclaw/openclaw.json
# Отредактировать пути workspace и API-ключи
```

### 3. Развернуть workspace'ы

```bash
chmod +x scripts/setup.sh
./scripts/setup.sh
```

### 4. Заполнить секреты

```bash
cp .env.example .env
# Заполнить: METRICOOL_API_KEY, NOUSHEN_CRM_*, FB_*, и т.д.
```

### 5. Запустить Gateway

```bash
openclaw gateway
```

## Команды (через чат с coordinator)

| Команда | Действие |
|---------|----------|
| `/pipeline <url>` | Полный цикл: парсинг → CRM → видео → очередь публикации |
| `/parse <url>` | Только парсинг |
| `/crm <listing_id>` | Записать в CRM |
| `/video <listing_id>` | Сгенерировать видео |
| `/publish-social <listing_id>` | Instagram / TikTok / YouTube через Metricool |
| `/publish-fb-marketplace <listing_id>` | Facebook Marketplace |
| `/publish-fb-groups <listing_id>` | Facebook Groups |

## Структура проекта

```
├── deploy/openclaw.json.example   # Конфиг multi-agent
├── workspaces/
│   ├── coordinator/               # Оркестратор
│   ├── parser/
│   ├── crm/
│   ├── video/
│   └── publisher/                 # 3 skill'а публикации
├── data/                          # Обмен данными (media в .gitignore)
├── scripts/setup.sh               # Деплой workspace'ов
└── .env.example                   # Шаблон секретов
```

## Что настроить перед продакшеном

- [ ] API-ключ Metricool + подключённые аккаунты IG/TikTok/YouTube
- [ ] API или webhook Noushen CRM (поля в `workspaces/crm/skills/noushen-crm/`)
- [ ] Facebook: токены для Marketplace и Groups (отдельные skill'ы)
- [ ] Источник объявлений: URL-паттерны в skill `parse-listing`
- [ ] Модели и лимиты в `openclaw.json` (timeout для video-агента — 900s+)

## Лицензия

Private — внутренний проект.
