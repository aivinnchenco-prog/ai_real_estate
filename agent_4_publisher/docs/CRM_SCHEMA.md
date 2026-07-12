# CRM Notion — Схема и контракт данных

**База:** `e817ce50e78849928b86c9c9fc1fbcf7`  
**Статус:** ✅ Сверена с Notion API (порядок колонок — июль 2026)

## Порядок колонок в таблице (Notion UI)

> Порядок не влияет на API; актуальный список — в `config/publisher.json` → `schema_column_order`.  
> Проверка: `python3 scripts/verify_notion_schema.py`

| # | Колонка | Тип |
|---|---------|-----|
| 1 | Статус | status |
| 2 | Тип аренды | select |
| 3 | Описание для Telegram | rich_text |
| 4 | agent6_taken_at | date |
| 5 | Источник объявления | url |
| 6 | video_url_Seedance | url |
| 7 | Цена за год | number |
| 8 | Можно с питомцами | checkbox |
| 9 | last_error | rich_text |
| 10 | post_url_telegram | url |
| 11 | Дата добавления | created_time |
| 12 | Площадь участка (м²) | number |
| 13 | Описание сец.сети | rich_text |
| 14 | metricool_post_group_id | rich_text _(legacy)_ |
| 15 | Google Maps | url |
| 16 | Количество сан.узлов | number |
| 17 | Удобства | multi_select |
| 18 | post_url_youtube | url |
| 19 | Описание | rich_text |
| 20 | Описание для FB Marketplace | rich_text |
| 21 | Количество комнат | number |
| 22 | agent6_video_done | checkbox |
| 23 | post_url_linkedin | url |
| 24 | Адрес | rich_text |
| 25 | Цена за месяц | number |
| 26 | post_url_x | url |
| 27 | WhatsApp контакт | rich_text |
| 28 | Залог | number |
| 29 | **Фото** | url |
| 30 | agent6_carousel_done | checkbox |
| 31 | post_url_facebook | url |
| 32 | Объект ID | rich_text |
| 33 | Локация | place |
| 34 | Этаж | number |
| 35 | post_url_threads | url |
| 36 | agent6_mode | select |
| 37 | Год постройки | number |
| 38 | Вид | select |
| 39 | agent6_log | rich_text |
| 40 | video_url_vertical | url |
| 41 | post_url_tiktok | url |
| 42 | Владелец / Агент | rich_text |
| 43 | metricool_post_id | rich_text |
| 44 | Тип жилья | select |
| 45 | Район | rich_text |
| 46 | error_count | number |
| 47 | post_url_instagram_carousel | url |
| 48 | post_url_instagram_reel | url |
| 49 | agent6_locked | checkbox |
| 50 | Telegram контакт | rich_text |
| 51 | Название объекта | title |

## Agent 6 — служебные поля

| Поле | Тип | Назначение |
|------|-----|------------|
| agent6_locked | checkbox | Объект взят, без авто-повтора |
| agent6_taken_at | date | Когда Agent 6 взял объект |
| agent6_carousel_done | checkbox | Карусель отправлена в Metricool |
| agent6_video_done | checkbox | Видео-пост отправлен |
| agent6_mode | select | auto / carousel / video |
| agent6_log | rich_text | Лог Agent 6 |

## Основные поля (обязательные)

| Поле | Тип | Описание | Формат |
|------|-----|---------|--------|
| **Название объекта** | Title | Основное имя объекта | Уникально |
| **Объект ID** | Text | Уникальный идентификатор для файлов и поисков | `OBJ_{timestamp}_{hash}` |
| **Статус** | Status | Текущий этап в конвейере | `ready_for_video` \| `video_in_progress` \| `ready_to_post` \| `video_failed` |

## Основные данные объекта

| Поле | Тип | Описание |
|------|-----|---------|
| **Адрес** | Rich Text | Полный адрес объекта |
| **Локация** | Place | Координаты для карты (заполняется автоматически) |
| **Район** | Rich Text | Муниципальный район или область |
| **Google Maps** | URL | Ссылка на объект в Google Maps |

## Характеристики

| Поле | Тип | Описания |
|------|-----|----------|
| **Тип жилья** | Select | Квартира / Дом / Студия / Таунхаус / Вилла / Комната / Кондоминиум |
| **Количество комнат** | Number | Кол-во спален |
| **Площадь участка (м²)** | Number | Общая площадь в кв. метрах |
| **Этаж** | Number | На каком этаже |
| **Год постройки** | Number | Год строительства |
| **Вид** | Select | Горы / Море / Сад / Город / Парк / Бассейн |

## Условия аренды

| Поле | Тип | Описание |
|------|-----|---------|
| **Тип аренды** | Select | Долгосрочная / Краткосрочная / Любая |
| **Доступно с** | Date | Дата, с которой доступен объект |
| **Цена за месяц** | Number (฿) | Месячная аренда в батах |
| **Цена за год** | Number (฿) | Годовая аренда в батах |
| **Залог** | Number (₽) | Размер залога (в рублях, можно переконвертить) |
| **Можно с питомцами** | Checkbox | Разрешены ли домашние животные |

## Удобства и внешний вид

| Поле | Тип | Описание |
|------|-----|---------|
| **Удобства** | Multi-Select | Парковка, Балкон, Кондиционер, Интернет, Wi-Fi, Бассейн, Фитнес, Охрана 24/7 и др. |

## Медиа и ссылки

| Поле | Тип | Описание | Где хранится |
|------|-----|---------|-------------|
| **Фото** | URL | Основное фото объекта | Cloudflare R2: `/{object_id}/photos/main.jpg` |
| **video_url_vertical** | URL | Видео 9:16 FFmpeg reel (Agent 3) | Cloudflare R2: `/{object_id}/videos/vertical.mp4` |
| **video_url_Seedance** | URL | Видео 9:16 (Metricool) | Agent 6 → соцсети, текст из **Описание сец.сети** |
| **Описание** | Rich Text | Полное описание объекта (CRM, не для публикации Agent 6) |
| **Описание для Telegram** | Rich Text | Подпись для TG-канала | Agent 6 → `publish_telegram.py` |
| **Описание сец.сети** | Rich Text | Универсальная подпись для Metricool | Agent 6 → `publish_pipeline.py` |
| **metricool_post_id** | Rich Text | ID поста в Metricool после публикации (Agent 6) | Заполняется `publish_pipeline.py` |
| **post_url_instagram_carousel** | URL | Ссылка на карусель в Instagram | Agent 6 после публикации |
| **post_url_instagram_reel** | URL | Ссылка на Reel в Instagram | Agent 6 после публикации |
| **post_url_tiktok** | URL | Ссылка на пост в TikTok | Agent 6 после публикации |
| **post_url_x** | URL | Ссылка на пост в X.com | Agent 6 после публикации |
| **post_url_linkedin** | URL | Ссылка на пост в LinkedIn | Agent 6 после публикации |
| **post_url_facebook** | URL | Ссылка на пост в Facebook | Agent 6 после публикации |
| **post_url_youtube** | URL | Ссылка на пост в YouTube | Agent 6 после публикации |
| **post_url_threads** | URL | Ссылка на пост в Threads | Agent 6 после публикации |
| **post_url_telegram** | URL | Ссылка на пост в Telegram-канале | После публикации в TG |
| **publora_post_group_id** / **metricool_post_group_id** | Rich Text | _(legacy)_ старый ID из Publora | больше не используется |

## Описания для каналов

| Поле | Тип | Описание | Правила |
|------|-----|---------|--------|
| **Описание** | Rich Text | Полное, подробное описание объекта | Честные данные, геометрия реальная |
| **Описание для Telegram** | Rich Text | Оптимизировано для TG-канала | **Всегда включает:** +66625124001 в конце, метку `#obj_ID` для поиска |
| **Описание сец.сети** | Rich Text | Универсальное для Instagram, TikTok, X, LinkedIn, Facebook | Без ссылок на Airbnb; для Metricool |
| **Описание для FB Marketplace** | Rich Text | Оптимизировано для Facebook | **Всегда включает:** +66625124001, хэштеги для поиска |

## Контакты

| Поле | Тип | Описание |
|------|-----|---------|
| **Telegram контакт** | Rich Text | Контакт владельца в Telegram |
| **WhatsApp контакт** | Rich Text | WhatsApp номер для связи |
| **Владелец / Агент** | Rich Text | Имя ответственного лица |

## Служебные поля

| Поле | Тип | Описание |
|------|-----|---------|
| **Источник объявления** | URL | Исходная ссылка на объявление |
| **Дата добавления** | Created Time | Автоматическая метка создания |
| **error_count** | Number | Количество ошибок при обработке |
| **last_error** | Rich Text | Текст последней ошибки |
| **metricool_post_id** | Rich Text | ID поста в Metricool (Agent 6 Publisher) |
| **post_url_instagram_carousel** | URL | Ссылка на карусель в Instagram |
| **post_url_instagram_reel** | URL | Ссылка на Reel в Instagram |
| **post_url_tiktok** | URL | Ссылка на опубликованный пост в TikTok |
| **post_url_x** | URL | Ссылка на опубликованный пост в X.com |
| **post_url_linkedin** | URL | Ссылка на опубликованный пост в LinkedIn |
| **post_url_facebook** | URL | Ссылка на опубликованный пост в Facebook |
| **post_url_youtube** | URL | Ссылка на опубликованный пост в YouTube |
| **post_url_threads** | URL | Ссылка на опубликованный пост в Threads |
| **post_url_telegram** | URL | Ссылка на опубликованный пост в Telegram-канале |

## Статус-конвейер

```
Поток жизненного цикла объекта:
new → parsed → enriched → ready_for_video → video_in_progress
  → ready_to_post → (Agent 6: post_url_* + agent6_locked)

Доступные статусы в Notion:
- ready_for_video
- video_in_progress  (в т.ч. пока Agent 6 в работе)
- ready_to_post      (очередь публикации; после успеха остаётся, повтор блокирует agent6_locked)
- video_failed

Боковые статусы (неудачные ветки):
- video_failed (видео или публикация не удались)
```

## Контракт данных: Как работает система

### 1. **Идемпотентность**
- Перед обработкой объекта статус переводится в `{action}_in_progress`
- Если система упала, на перезагрузке объект остаётся в *_in_progress и пересчитывается
- Статус не двигается вперёд без подтверждённого успеха

### 2. **Ошибки и ретраи**
- При ошибке: `error_count += 1`, статус НЕ двигается
- Если `error_count > 3`: статус → `video_failed`, отправляется алёрт в служебный чат
- `last_error` сохраняет текст ошибки для отладки

### 3. **Поля, которые НИКОГДА не заполняются вручную**
- `Дата добавления` — автоматически от Notion
- `error_count`, `last_error`, `metricool_post_id`, `post_url_*` — только системой
- Статусы — только при успешной обработке

### 4. **Google Maps: Автоматический поиск**
- По полям `Адрес` + `Район` система ищет место в Google Maps
- Результат сохраняется в `Google Maps` как URL типа:
  ```
  https://www.google.com/maps/place/13.7563,100.5018
  ```

### 5. **Объект ID: Метка везде**
- Формируется при первом появлении объекта в системе
- Используется для:
  - Папок в R2: `real-estate-propertiess/{object_id}/photos/`, `/{object_id}/videos/`
  - Постов: включается хэштег `#obj_{object_id}` для быстрого поиска
  - Восстановления: если объект пересчитывается, ID не меняется

## Примеры заполнения

### Описание для Telegram
```
🏠 Уютная студия на 45м² в центре района Сукхумвит
🛏️ 1 комната, балкон с видом на город
❄️ Кондиционер, интернет, паркинг
💰 15,000 ฿/месяц (180,000 ฿/год)
📅 Доступно с 15 июля

Прекрасное место для долгосрочной аренды. Охрана 24/7, близко к метро.

☎️ Связь: +66625124001
🔍 #obj_OBJ_20260630_a4f2e1
```

### Описание для Facebook
```
Сдается студия в центре города 🏠

Площадь: 45 м²
Комнаты: 1
Цена: 15,000 батов/месяц
Вид: На город

Удобства: Балкон, кондиционер, интернет, парковка, охрана 24/7

Контакт для связи: +66625124001

#квартира #аренда #студия #центр #obj_OBJ_20260630_a4f2e1
```
