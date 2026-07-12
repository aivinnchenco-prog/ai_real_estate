# 🏠 Real Estate Automation — Полная документация

## 📍 Основные документы

### 🚀 Стартовые
1. **[SETUP_COMPLETE.md](SETUP_COMPLETE.md)** — ✅ Что готово, что нужно
2. **[CONSTITUTIO.md](CONSTITUTION.md)** (если создавали) — Общая архитектура проекта

### 📚 Рабочие
3. **[CRM_SCHEMA.md](CRM_SCHEMA.md)** — Полная схема Notion базы (45+ полей)
4. **[WORKFLOW.md](WORKFLOW.md)** — Пошаговый цикл от данных до лида
5. **[INPUT_FORMAT.md](INPUT_FORMAT.md)** — Как отправить спарсенные данные

### 🛠️ Инструменты
6. **[real_estate_cli.sh](real_estate_cli.sh)** — Shell CLI для Notion
7. **[real_estate_handler.py](real_estate_handler.py)** — Python модуль (NotionCRM, CloudflareR2, DescriptionGenerator)
8. **[.env.real-estate](.env.real-estate)** — Credentials (НЕ коммитить!)

---

## 🎯 Quick Start

### 1️⃣ Отправь спарсенный объект

**Формат JSON:**
```json
{
  "title": "Studio at Sukhumvit 45",
  "address": "234 Sukhumvit Soi 15, Bangkok",
  "rooms": 1,
  "area": 45,
  "price_monthly": 15000,
  "description": "Beautiful studio...",
  "amenities": ["Balcony", "AC", "Internet"],
  "photos": ["https://example.com/photo1.jpg"],
  "source_url": "https://original-source.com/listing"
}
```

**Или простой текст:**
```
Название: Studio at Sukhumvit 45
Адрес: 234 Sukhumvit Soi 15, Bangkok
Комнаты: 1
Площадь: 45 м²
Цена: 15,000 ฿/месяц
Описание: Beautiful studio...
Фото: https://example.com/photo1.jpg
```

→ Отправь в TG-бот

### 2️⃣ Я обработаю

```
[автоматически]
1. Парсинг и генерация Object ID (OBJ_YYYYMMDD_hash)
2. Создание в Notion CRM (статус: ready_for_video)
3. Загрузка фото в Cloudflare R2
4. Поиск в Google Maps + обогащение
5. Генерация описаний для TG и Facebook
6. Обновление статуса → ready_to_post
7. Публикация в TG-канал
8. Публикация в Facebook Marketplace
9. Lead Bot слушает replies
10. Менеджер закрывает сделку
```

---

## 🏗️ Архитектура

```
NOTION CRM (источник правды)
├── Название объекта
├── Объект ID (метка везде)
├── Статус (конвейер)
├── Характеристики (комнаты, площадь, цена)
├── Описания (TG, FB)
├── Фото (URL в R2)
└── Видео (vertical, square, wide URLs)

CLOUDFLARE R2 (хранилище медиа)
/{object_id}/
  ├── photos/*.jpg
  └── videos/*.mp4

GOOGLE MAPS (геолокация)
← Автоматический поиск по адресу

TELEGRAM (дистрибуция + квалификация)
├── @channel_name (публикация)
└── @lead_bot (квалификация лидов)

FACEBOOK (дистрибуция)
├── Marketplace
└── Page
```

---

## 📊 Статус-конвейер

```
┌─────────────────────────────────────────────┐
│         ЖИЗНЕННЫЙ ЦИКЛ ОБЪЕКТА             │
└─────────────────────────────────────────────┘

new
  ↓
parsed (данные распарсены)
  ↓
enriched (обогащены описания, Google Maps)
  ↓
ready_for_video (готов к рендеру видео)
  ↓
video_in_progress (рендер видео идёт)
  ├→ ✅ готово → ready_to_post
  └→ ❌ ошибка → video_failed (+ алёрт)
  ↓
ready_to_post (готов к публикации)
  ↓
published (опубликован в TG и FB)
  ↓
live (лиды поступают через Lead Bot)

───────────────────────────────────────────

Боковые статусы:
- duplicate (дубликат найден)
- needs_review (требует проверки)
- archived (архивирован)
```

---

## 🔑 Ключевые компоненты

### Notion CRM
- **База:** `e817ce50e78849928b86c9c9fc1fbcf7`
- **Таблица:** Аренда недвижимости
- **Полей:** 45+
- **Контракт:** фиксированные имена полей

### Cloudflare R2
- **Bucket:** `real-estate-propertiess`
- **Base URL:** `https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev`
- **Структура:** `/{object_id}/photos/`, `/{object_id}/videos/`

### Contact
- **Phone:** `+66625124001` (всегда в описаниях)

---

## 🎯 Что дальше

### Неделя 1: Тестирование
- [ ] Отправить 5 тестовых объектов
- [ ] Проверить весь конвейер до ready_to_post
- [ ] Создать TG-канал для публикаций

### Неделя 2: Интеграция
- [ ] Создать TG-бот для входящих данных
- [ ] Создать TG-бот для Lead Bot
- [ ] Подключить Facebook Marketplace

### Неделя 3: Масштабирование
- [ ] Запустить /render для видео
- [ ] Настроить параллельную обработку (10+ объектов одновременно)
- [ ] Добавить алёрты в служебный чат

### Неделя 4+: Оптимизация
- [ ] Добавить Instagram (если нужно)
- [ ] Улучшить Lead Bot логику
- [ ] Настроить аналитику и KPI

---

## 💾 Файлы в проекте

```
workspace/
├── README.md                      ← Ты здесь
├── SETUP_COMPLETE.md              ← Что готово
├── CRM_SCHEMA.md                  ← Схема Notion
├── WORKFLOW.md                    ← Пошаговый процесс
├── INPUT_FORMAT.md                ← Как отправить данные
├── real_estate_cli.sh             ← Shell скрипт
├── real_estate_handler.py         ← Python модуль
├── .env.real-estate               ← Credentials (gitignore!)
└── memory/
    └── [daily notes]
```

---

## ⚙️ Быстрые команды

```bash
# Проверить все поля CRM
./real_estate_cli.sh properties

# Список объектов (ready_for_video)
./real_estate_cli.sh list ready_for_video

# Сгенерировать Object ID
./real_estate_cli.sh generate-id "Название" "Адрес"

# Получить объект по ID
./real_estate_cli.sh get <page_id>
```

---

## 🚀 Когда отправлять первый объект

**Готово сейчас!**

```
Отправь JSON или текст в TG-бот →
Я создам в Notion → 
Загружу фото и карты →
Сгенерирую описания →
Обновлю статус
```

Время обработки: ~10 минут (без видео), ~20 минут (с видео)

---

## 📞 Контакты

- **Телефон:** `+66625124001` (для объектов)
- **Notion Base:** https://app.notion.com/p/e817ce50e78849928b86c9c9fc1fbcf7
- **R2 Public Base:** https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev

---

## ✅ Чек-лист готовности

- [x] Notion API интегрирован
- [x] Cloudflare R2 настроен
- [x] CRM база создана (45+ полей)
- [x] Object ID генератор готов
- [x] Description generator готов
- [x] Shell CLI готов
- [x] Python модуль готов (требует requests)
- [x] Документация полная
- [ ] TG-бот для входящих (требует разработки)
- [ ] TG-бот для квалификации (требует разработки)
- [ ] /render сервис (требует разработки)
- [ ] Facebook интеграция (требует токена)

---

## 🎓 Обучающие материалы

**Что делать, если что-то сломалось:**

1. Проверить `last_error` в Notion
2. Увеличить `error_count` (или сбросить)
3. Пересчитать через ручной ретрай
4. Алёрт в служебный чат Telegram

**Если объект — дубликат:**
- Статус → `duplicate`
- Сохранить ID оригинала

**Если что-то непонятно:**
- Читай [WORKFLOW.md](WORKFLOW.md)
- Смотри примеры в [INPUT_FORMAT.md](INPUT_FORMAT.md)

---

🚀 **Система готова к первому объекту!**

Жди входящих данных и начинаем тестирование. 🎯
