# 🏢 Real Estate — память агента

## 🔴 СТОП-ПОИНТ (2026-07-06) — читай перед работой

**Полный handoff:** [`SESSION_HANDOFF.md`](SESSION_HANDOFF.md)

**Кратко:**
- Папка: `Desktop/Агенты/Real Estate Agent/agent 4`
- Код: `_import/assistant-media/` (deploy archive **устарел**)
- Agent 3: FFmpeg 9:16 + Seedance **параллельно**; 24/7 `./start.sh`
- **2026-07-06:** архив Higgsfield → `~/Desktop/higgsfield-seedance-agent-20260706.tar.gz`
- **2026-07-06:** Notion «Цена за месяц» → формат `number` (без ฿)

**Не сделано:** VPS деплой, Higgsfield OAuth на сервере, credits, E2E, Agent 6 боевой.

---

## ⚠️ ГЛАВНОЕ: читай PLAYBOOK.md

Все входящие объекты обрабатываются **только по `PLAYBOOK.md`**.  
Браузер и парсинг Airbnb **запрещены**. Данные — только от пользователя + скрипты.

## WAIT 150 SECONDS AFTER EACH PHOTO BATCH

**Agent 2 (Strukturator) ВСЕГДА:**
1. Получает описание → `media_batch_gate.py init`
2. Каждая пачка фото (~10 шт.) → сохранить → `media_batch_gate.py bump --count N`
3. **После КАЖДОЙ пачки ждёт 150 секунд** (таймер сбрасывается на новую пачку)
4. Когда 150 сек без новых фото → `ready` → структуризация
5. Загружает в R2 + заполняет Notion (Google Maps, источник, район — обязательно!)
6. Статус → `ready_for_video`
7. **ТОЛЬКО ТОГДА** Agent 3

**Неправильно:** один wait 150 сек после первых фото  
**Правильно:** 150 сек после каждой новой пачки, пока фото не перестанут приходить

**Правильный workflow:**
```
Описание → init session
    ↓
Пачка 1 (10 фото) → bump → WAIT 150s
    ↓
Пачка 2 (10 фото) → bump → WAIT 150s
    ↓
...нет новых 150s → ready
    ↓
agent2_structurize.py → Notion + R2
    ↓
agent3_video.mjs → видео 9:16 (reel) + Seedance параллельно
    ↓
ready_to_post → Agent 6 Publisher
```

## Архитектура (MVP) - С ИСПРАВЛЕННОЙ ЛОГИКОЙ

### **Агент 1: Parser** (внешний)
- Парсит объявления недвижимости (ручная выборка)
- Отправляет мне: описание + фотографии
- Запускает цепь

### **Агент 2: Я (Strukturator)** ← ВЫ ЗДЕСЬ
**Что делаю:**
- Загружаю фото в Cloudflare R2 (`/property-{ID}/photo_*.jpg`)
- Структурирую в Notion CRM
- Присваиваю уникальный ID каждому объекту

**ID система:**
- Уникален для каждого property
- Используется везде: Notion, R2, видео, соцсети
- Помогает найти объект при заявке от клиента

**R2 структура:**
```
/property-{ID}/
  photo_101.jpg
  photo_102.jpg
  ...
  index.html (галерея)
  
/music/
  track_001.mp3
  track_002.mp3
  ...
```

---

### **Агент 3: Video Creator**
**Задача:** Создает видео из фото + музыка

**Два метода:**
1. **Бесплатный (локальный сервер)**
   - Скрипт (Remotion или аналог)
   - Склейка: 2.5 сек на кадр
   - С музыкой из библиотеки `/music/`
   - Три формата: 1:1, 3:4, 9:16

2. **Платный (Hixfield + Cadence 2.0)**
   - API к Hixfield
   - Нейросеть генерирует 15-20 сек видео
   - Prompt: "Киношная проходка, покажи интерьер"
   - Cadence генерирует музыку автоматически
   - Три формата: 1:1, 3:4, 9:16

**Output:**
- Видео загружаются в R2 (`/property-{ID}/video_*.mp4`)
- Ссылки → в Notion (колонки: "Видео (бесплатное)", "Видео (Cadence)")

---

### **Агент 4: Publisher** 
**Вариант:** Один агент + две функции (или два отдельных)

**Функция 1: Соцсети** (Instagram, TikTok, YouTube)
- Публикует видео (все три формата)
- Публикует карусели красивых фото
  - ⚠️ ТОЛЬКО красивые кадры (интерьер, вид, дизайн)
  - НЕ утюги, фены, вилки, унитазы
- Каждый пост содержит **ID объекта** (в описании)

**Функция 2: Facebook** (Marketplace + группы)
- Отдельная логика для Facebook
- Также с ID в описании

---

### **Агент 5: Qualifier** (Lead Manager)
**Работает 24/7** в Telegram + WhatsApp

**Сценарий 1: Клиент нашел объект**
- Отправляет ссылку на видео/пост с ID
- Бот найти объект по ID в Notion
- Показывает полное описание + фото
- Предлагает записаться

**Сценарий 2: Клиент не выбрал**
- Запрашивает: бюджет, район, даты, кол-во людей
- По критериям показывает подходящие объекты
- Добавляет заявку в CRM (Bitrix или AmoCRM)

---

## Workflow (Автоматический)

```
Агент 1 (Parser) отправляет мне фото + описание
        ↓
Я загружаю в R2 + структурирую в Notion + присваиваю ID
        ↓
🔄 АВТОМАТ ЗАПУСКАЕТСЯ:
        ↓
Агент 3: Создает видео (локальное + Cadence)
        ↓
Агент 4: Публикует в соцсети + Facebook
        ↓
Агент 5: Ловит заявки 24/7 → добавляет в CRM
```

---

## Notion CRM - Структура

**Таблица Properties:**
- ID (уникальный)
- Название
- Описание
- Район/Локация
- Цена
- Ссылка на галерею (R2)
- Видео (бесплатное) - ссылка на mp4
- Видео (Cadence) - ссылка на mp4
- Статус (активно/архив)
- Заявки (связь с таблицей Leads)

**Таблица Leads:**
- ID заявки
- Имя клиента
- Telegram/WhatsApp
- Property ID (связь)
- Бюджет
- Статус (квалифицирована/ожидание)

---

## R2 Организация

```
/property-001/
  photo_101.jpg
  photo_102.jpg
  ...photo_118.jpg
  index.html (галерея)
  video_1x1.mp4
  video_3x4.mp4
  video_9x16.mp4
  video_cadence_1x1.mp4
  video_cadence_3x4.mp4
  video_cadence_9x16.mp4

/property-002/
  [аналогично]

/music/
  track_001.mp3
  track_002.mp3
  ...
```

---

## Инструменты

- `r2_upload_v2.py` - загрузка в R2
- `gallery.html` - галерея фото (темный фон)
- Remotion или FFmpeg - видео монтаж
- Hixfield API - для Cadence генерации
- Telegram/WhatsApp API - для квалификатора

---

## Статус

✅ Агент 2 готов (загрузка + галерея + Notion)
✅ Агент 3 - готов (curator + render_reel)
⏳ Агент 4 - в разработке  
⏳ Агент 5 - в разработке

**Текущий проект:** property-001 (18 фото в R2, галерея работает)

**НАРЯД 2 ЗАВЕРШЁН (30 июня, 17:03 UTC):**
- Curator service (CLIP ViT-B-32 CPU-only) запущен на :8077
- r2list.mjs для листинга R2 и presigned-URLs
- index.ts с двумя тулами: select_best_images + render_reel
- Тестирование: curator выбрал 6 лучших фото из property-001, разнообразно по комнатам
- Higgsfield/Cadence — ждут отдельного наряда

## Обновление: Музыка добавлена (30 июня, 17:08 UTC)

**3 трека в R2 `/music/`:**
- track_001.mp3 (373 KB)
- track_002.mp3 (188 KB)
- track_003.mp3 (388 KB)

**Новый тул `get_music_track()`:**
- Возвращает трек по очереди циклически
- Каждый вызов переходит к следующему треку
- Идеален для автоматической ротации музыки

**Цепочка работы:**
1. select_best_images(property-001) → 6 лучших фото
2. get_music_track() → track_001.mp3
3. render_reel(property-001, images, track_001.mp3) → video.mp4
4. Следующий вызов get_music_track() → track_002.mp3

## ТЕСТ ВИДЕО ЗАВЕРШЁН (30 июня, 17:16 UTC)

**Полная цепочка для property-001:**

1. **select_best_images(property-001, top_k=6)**
   - Отобрано: photo_102, photo_103, photo_106, photo_108, photo_107, photo_104
   - Категории: 2x living, 1x dining, 3x kitchen
   - Отклонено: 0 (нет технического мусора)

2. **get_music_track()**
   - Выбран трек: music/track_001.mp3 (373 KB)
   - Следующий вызов вернёт: music/track_002.mp3

3. **render_reel(property-001, images, music)**
   - Скачано: 6 фото + музыка из R2
   - Монтаж: FFmpeg, 6 кадров × 2.5 сек = 15 сек
   - Видео: 1080×1920 (вертикальное), H.264, AAC
   - Размер: 796 KB
   - ✅ Загружено в R2

**Результат видео:**
- Ключ R2: `property-001/video.mp4`
- Публичный URL: https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/property-001/video.mp4
- Доступно: ✅ (HTTP 200, video/mp4)

**Готово к:**
- Загрузке в Notion (ссылка в поле "video_url_vertical")
- Публикации в социальные сети (Reels, TikTok, Shorts)
- Следующего видео (get_music_track() → track_002)

## ОБНОВЛЕНИЕ: Три формата + Зум эффект (30 июня, 17:18 UTC)

**renderReel.mjs теперь создает три версии видео:**

1. **1:1 (1080×1080)** — Квадратное (Instagram Feed)
2. **3:4 (1080×1440)** — Портрет (Pinterest, Facebook)
3. **9:16 (1080×1920)** — Вертикальное (Reels, TikTok, Shorts)

**Эффект**: Плавное приближение (zoom in 1.15x) на каждом кадре, центрированное
- Использует FFmpeg zoompan фильтр
- Эффект заполняет все 2.5 секунды кадра

**Output**: Возвращает словарь с тремя URL:
```json
{
  "1x1": "https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/property-001/video_1x1.mp4",
  "3x4": "https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/property-001/video_3x4.mp4",
  "9x16": "https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/property-001/video_9x16.mp4"
}
```

**Готово для публикации:**
- Instagram (все три формата для разных контекстов)
- TikTok (9:16)
- YouTube Shorts (9:16)
- Pinterest (3:4)
- Facebook (все форматы)

## ПОЛНЫЙ PIPELINE ЗАВЕРШЁН (30 июня, 17:27 UTC)

**Автоматизированная цепочка от получения объекта до видео:**

### Этапы:
1. **CRM Lookup** → Поиск объекта в Notion Database
   - Объект ID: property-001
   - Найден: ✅ (page_id: 38f2c251-5061-81a6-878e-c327922534c3)

2. **CURATOR** → Выбор лучших 6 фото
   - Используется CLIP ViT-B-32 (CPU, :8077)
   - Отобрано: photo_102, photo_110, photo_106, photo_113, photo_118, photo_109
   - Время: <2 сек

3. **MUSIC** → Циклическая ротация треков
   - Трек #1: music/track_001.mp3
   - Трек #2: music/track_002.mp3 (для следующего видео)
   - Трек #3: music/track_003.mp3 (для 3-го видео)
   - Логика: каждый новый видео → следующий трек по очереди

4. **RENDER** → 3 видео формата (параллельно)
   - **1x1 (1080×1080)** → Instagram Feed
     - URL: https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/property-001/video_1x1.mp4
     - Время: ~30 сек
   
   - **3x4 (1080×1440)** → Pinterest, Facebook
     - URL: https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/property-001/video_3x4.mp4
     - Время: ~30 сек
   
   - **9x16 (1080×1920)** → Reels, TikTok, YouTube Shorts
     - URL: https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/property-001/video_9x16.mp4
     - Время: ~30 сек

5. **CRM UPDATE** → Запись ссылок в Notion
   - Обновлены поля: video_url_square, video_url_wide, video_url_vertical
   - Статус: ready_for_video → ready_for_publishing

### Итого время: ~3 минуты (из них 80% на FFmpeg рендер)

### Результат:
```json
{
  "object_id": "property-001",
  "curator_selected": 6,
  "music_track": "music/track_001.mp3",
  "videos": {
    "1x1": "https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/property-001/video_1x1.mp4",
    "3x4": "https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/property-001/video_3x4.mp4",
    "9x16": "https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/property-001/video_9x16.mp4"
  },
  "notion_updated": true,
  "next_music_track": "music/track_002.mp3"
}
```

## Готово к:
✅ **Агент 4 (Publisher)** — Публикация в Instagram/TikTok/YouTube/Facebook
✅ **Агент 5 (Qualifier)** — Обработка заявок клиентов из соцсетей
✅ **Масштабирование** — Полностью автоматизированный процесс для новых объектов
