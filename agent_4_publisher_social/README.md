# Publisher social

Публикация объектов недвижимости **с Android-телефона** (один аккаунт на сеть).

> Канон в монорепе: `Real Estate Agent/agent_4_publisher_social/`.  
> Отдельный репозиторий [`publisher-social`](https://github.com/bvinnchenco-cmyk/publisher-social) — зеркало/Termux; Agent 4 смотрит сюда через `phone_publisher.project_path`.

Источник контента — тот же пайплайн, что у Real Estate Agent:

- **Notion** CRM (`ready_to_post`)
- **Cloudflare R2** — фото/карусель/видео (публичные URL из Notion)

Каналы (все через телефон, без Metricool / Playwright):

| Канал | Контент |
|--------|---------|
| TikTok | видео (Seedance) |
| TikTok Carousel | фото (через +60 мин после TikTok) |
| Instagram Reel | видео |
| Instagram Carousel | фото (через +60 мин после Reel) |
| YouTube Shorts | видео (Seedance) |
| LinkedIn | фото + описание |
| Twitter / X | **видео** (по умолчанию) или фото (макс 4) |
| Facebook Groups | фото + описание (`caption_fb`) |
| Facebook Marketplace | фото из **`brand_open_home_url`** (последний в очереди) |

## Статус (live на телефоне)

| Канал | Статус |
|--------|--------|
| **TikTok** | ✅ live |
| **TikTok Carousel** | ✅ код + отложенный слот |
| **Instagram Reel** | ✅ live; URL позже через API |
| **Instagram Carousel** | ✅ код + отложенный слот |
| **YouTube Shorts** | ✅ live; URL позже через API |
| **LinkedIn** | ✅ live; URL позже через API |
| **Twitter / X** | ✅ live видео; URL позже через API |
| **FB Groups** | ✅ live (группа из `config/fb_groups_list.txt`) |
| **FB Marketplace** | ✅ live; фото только `brand_open_home`; URL позже через API |

Порядок `publish-all`: video → carousel → **fb_marketplace последним**. В live-режиме
за один запуск выполняется только ближайший канал, после чего процесс завершается
для отчёта и ручной проверки. Автоматические live-повторы отключены.

Общее:
1. Notion → R2 → скачивание → `adb push` (carousel → `publisher_social`, Marketplace → `brand_open_home`)
2. UI через **uiautomator2**
3. Публикация только с `--live` (иначе `--ui` — стоп перед постом)

Подробности: [`docs/CHANNELS.md`](docs/CHANNELS.md)

## Быстрый старт

```bash
cd agent_4_publisher_social   # или: …/Агенты/Publisher social (legacy)
cp .env.example .env
# NOTION_API_KEY + NOTION_DB_ID
# либо ключи в Real Estate Agent/.env — подхватятся автоматически
# ANDROID_SERIAL=…  # если телефон не единственный в adb devices
```

Телефон: USB-отладка или `adb connect <ip>:5555`.

```bash
export PYTHONPATH=src

# Проверить телефон
python3 -m publisher_social check-device

# Посмотреть очередь (dry-run, ничего не постит)
python3 -m publisher_social queue --dry-run

# Скачать медиа одного объекта
python3 -m publisher_social prepare --page-id PAGE_ID

# Скачать + залить на телефон
python3 -m publisher_social prepare --page-id PAGE_ID --push-media

# TikTok видео
python3 -m publisher_social publish --page-id PAGE_ID --channel tiktok --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel tiktok --live

# TikTok карусель (фото)
python3 -m publisher_social publish --page-id PAGE_ID --channel tiktok_carousel --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel tiktok_carousel --live

# FB Marketplace (аренда; личный профиль)
python3 -m publisher_social publish --page-id PAGE_ID --channel fb_marketplace --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel fb_marketplace --live

# LinkedIn
python3 -m publisher_social publish --page-id PAGE_ID --channel linkedin --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel linkedin --live

# Twitter / X
python3 -m publisher_social publish --page-id PAGE_ID --channel twitter --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel twitter --live

# YouTube Shorts
python3 -m publisher_social publish --page-id PAGE_ID --channel youtube_shorts --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel youtube_shorts --live

# Полный прогон объекта (видео + карусели сразу; TikTok/IG карусели +1ч в очередь)
python3 -m publisher_social publish-all --page-id PAGE_ID --live

# Отложенные карусели (cron каждые 15 мин на сервере)
python3 -m publisher_social publish-scheduled --live
```

Обычный `queue` не публикует TikTok/Instagram carousel раньше их
`publish_after`. При этом `queue --live` сам сначала забирает уже наступившие
scheduled-задачи, поэтому для одного автономного раннера отдельный процесс
`publish-scheduled` не обязателен.

Карусели берутся из колонки Notion `carousel_url`, видео — из `video_url_Seedance`.  
После `--live` телефон **не получает ссылки публикаций**: он не открывает профиль,
share-sheet и clipboard. Успешное нажатие публикации сохраняется со статусом
`accepted`, а поля `post_url_*` позже заполняет отдельная API-интеграция.

Видео на телефоне выбирается одинаково во всех видеосценариях: открыть системную
галерею/раздел **«Видео»** и выбрать первое (самое новое, последнее добавленное)
видео. Отдельный альбом объекта для выбора ролика не используется.

Команда `capture-url` и автоматический захват URL через Android отключены
конфигурацией `post_url_capture.enabled=false`.

Нужны: `export PATH="$HOME/bin:$PATH"` и `pip install -r requirements.txt`.

Группы FB — в `config/fb_groups_list.txt` (по ссылке на строку).

## Структура

```
Publisher social/
├── config/
│   ├── publisher.json      # каналы, поля Notion, лимиты
│   ├── android.json        # ADB, пакеты приложений
│   └── fb_groups_list.txt
├── src/publisher_social/
│   ├── pipeline.py         # Notion → job → каналы
│   ├── media.py            # R2 gallery / download
│   ├── android/adb.py      # ADB push / open app
│   └── channels/           # tiktok, instagram, fb_*
└── data/
    ├── media/              # кэш файлов
    ├── jobs/               # снимок job JSON
    └── state.json          # что уже опубликовано с телефона
```

## Важно

- Не пересекаемся с Metricool: локальный `data/state.json` защищает от повторной публикации; `post_url_*` заполняются позже через API.  
- Все команды, управляющие телефоном или state, используют единый
  `data/publisher.lock`. Если телефон занят, второй процесс завершается с кодом 3
  и не вмешивается в текущий UI-сценарий.
- `state.json` пишется атомарно. Не доставленные обновления Notion остаются в
  `notion_outbox` и повторяются следующим live-запуском без повторной публикации.
- Успешная публикация с телефона без URL имеет статус `accepted`.
  Автоматическая перепубликация такого канала запрещена.
- Лимиты `limits.per_channel_daily_max` применяются ко всем live-публикациям и
  считаются по `timezone` проекта.
- Metricool-ветку в `agent_4_publisher` для этих каналов лучше выключить, когда телефонный постинг станет боевым.  
- Опциональные колонки Notion (`phone_publisher_locked`, …) зарезервированы
  для межхостовой координации; текущий lock защищает процессы одного хоста.
  До реализации distributed lock разрешён только один активный live-хост:
  либо сервер, либо Termux. Одновременный `--live` с обоих хостов может
  управлять одним телефоном параллельно и поэтому не поддерживается.

## Деплой (автономный сервер)

```bash
# каждые 15 мин — due scheduled, затем обычная очередь ready_to_post
*/15 * * * * cd /path/to/publisher && PYTHONPATH=src python3 -m publisher_social queue --live

# Необязательный отдельный обработчик scheduled. Единый lock исключает гонку,
# но при наличии queue выше отдельная строка обычно не нужна.
*/15 * * * * cd /path/to/publisher && PYTHONPATH=src python3 -m publisher_social publish-scheduled --live
```

Телефон должен быть подключён по USB/Wi‑Fi ADB (`adb devices`). Состояние — в `data/state.json` (включая `scheduled`).
Каждый cron-вызов завершает максимум один фактический live-канал. На сервере
stdout/stderr этой команды необходимо подключить к журналу/алертингу
планировщика; долговечный Telegram-outbox из коробки реализован в
`termux/bin/run_loop.sh`.

## Дальше

1. Подключить телефон по Wi‑Fi ADB  
2. Cron/Termux: один `queue --live` — он также потребляет due scheduled
