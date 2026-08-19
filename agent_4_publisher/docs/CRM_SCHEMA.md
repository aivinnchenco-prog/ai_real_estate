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
| 4 | Дата и время публикации | date + time |
| 5 | agent6_taken_at | date |
| 6 | Источник объявления | url |
| 7 | video_url_Seedance | url |
| 8 | Цена за год | number |
| 9 | Можно с питомцами | checkbox |
| 10 | last_error | rich_text |
| 11 | post_url_telegram | url |
| 12 | Дата добавления | created_time |
| 13 | Площадь участка (м²) | number |
| 14 | Описание сец.сети | rich_text |
| 15 | metricool_post_group_id | rich_text _(legacy)_ |
| 16 | Google Maps | url |
| 17 | Количество сан.узлов | number |
| 18 | Удобства | multi_select |
| 19 | post_url_youtube | url |
| 20 | Описание | rich_text |
| 21 | Описание для FB Marketplace | rich_text |
| 22 | Количество комнат | number |
| 23 | agent6_video_done | checkbox |
| 24 | post_url_linkedin | url |
| 25 | Адрес | rich_text |
| 26 | Цена за месяц | number |
| 27 | post_url_x | url |
| 28 | WhatsApp контакт | rich_text |
| 29 | Залог | number |
| 30 | **Фото** | url |
| 31 | agent6_carousel_done | checkbox |
| 32 | post_url_facebook | url |
| 33 | Объект ID | rich_text |
| 34 | Локация | place |
| 35 | Этаж | number |
| 36 | post_url_threads | url |
| 37 | agent6_mode | select |
| 38 | Год постройки | number |
| 39 | Вид | select |
| 40 | agent6_log | rich_text |
| 41 | video_url_vertical | url |
| 42 | post_url_tiktok | url |
| 43 | Владелец / Агент | rich_text |
| 44 | metricool_post_id | rich_text |
| 45 | Тип жилья | select |
| 46 | Район | rich_text |
| 47 | error_count | number |
| 48 | post_url_instagram_carousel | url |
| 49 | post_url_instagram_reel | url |
| 50 | agent6_locked | checkbox |
| 51 | Telegram контакт | rich_text |
| 52 | Название объекта | title |

## Agent 6 — служебные поля

| Поле | Тип | Назначение |
|------|-----|------------|
| **Дата и время публикации** | date + time | Когда и во сколько выйдет пост в соцсети (слот Metricool, TZ `Asia/Bangkok`). Для календарного вида Notion |
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
| **Дата и время публикации** | Date + time | Дата и время выхода поста в соцсети | Agent 6 → Metricool `publicationDate` (ISO с временем); календарный вид Notion |
| **metricool_post_id** | Rich Text | ID поста в Metricool после публикации (Agent 6) | Заполняется `publish_pipeline.py` |
| **post_url_instagram_carousel** | URL | Ссылка на карусель в Instagram | Agent 6 после публикации |
| **post_url_instagram_reel** | URL | Ссылка на Reel в Instagram | Agent 6 после публикации |
| **post_url_tiktok** | URL | Ссылка на видео в TikTok (`/@handle/video/…`) | Agent 6 после публикации |
| **post_url_tiktok_carousel** | URL | Ссылка на фото-карусель в TikTok (`/@handle/photo/…`) | Agent 6 после публикации |
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
| **post_url_tiktok** | URL | Ссылка на опубликованное видео в TikTok |
| **post_url_tiktok_carousel** | URL | Ссылка на опубликованную фото-карусель в TikTok |
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
