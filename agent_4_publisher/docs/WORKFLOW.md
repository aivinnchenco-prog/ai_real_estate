# WORKFLOW — Полный цикл обработки объекта

## 🎯 Цель
От спарсенных данных (фото + описание) к квалифицированному лиду, полностью автоматизировано.

## 📊 Архитектура потока

```
PARSED DATA (из парсера) 
    ↓
[1] СТРУКТУРИРОВАНИЕ (extract → Notion CRM)
    ↓ 
[2] ОБОГАЩЕНИЕ (Google Maps, описания TG/FB)
    ↓
[3] ВИДЕО (рендер из фото)
    ↓
[4] ДИСТРИБУЦИЯ (публикация в TG, FB)
    ↓
[5] КВАЛИФИКАЦИЯ (Lead Bot)
    ↓
ЖИВОЙ МЕНЕДЖЕР (закрытие сделки)
```

---

## [1] СТРУКТУРИРОВАНИЕ: Из хаоса в CRM

### Вход
**От парсера придёт в TG-бот сообщение типа:**
```
Название: Studio at Sukhumvit 45
Адрес: 234 Sukhumvit Soi 15, Bangkok
Цена: 15000 baht/month
Площадь: 45 m²
Комнаты: 1
Фото: https://example.com/photo1.jpg, https://example.com/photo2.jpg
Описание: Beautiful studio with balcony...
```

### Процесс

#### Шаг 1: Генерируем Object ID
```bash
./real_estate_cli.sh generate-id "Studio at Sukhumvit 45" "234 Sukhumvit Soi 15"
# Результат: OBJ_20260630_a4f2e1
```

#### Шаг 2: Структурируем и создаём в Notion

**Поля, которые заполняем СРАЗУ при создании:**

```json
{
  "Название объекта": {
    "title": [{"type": "text", "text": {"content": "Studio at Sukhumvit 45"}}]
  },
  "Объект ID": {
    "rich_text": [{"type": "text", "text": {"content": "OBJ_20260630_a4f2e1"}}]
  },
  "Статус": {
    "status": {"name": "ready_for_video"}
  },
  "Адрес": {
    "rich_text": [{"type": "text", "text": {"content": "234 Sukhumvit Soi 15, Bangkok"}}]
  },
  "Описание": {
    "rich_text": [{"type": "text", "text": {"content": "Beautiful studio with balcony..."}}]
  },
  "Цена за месяц": {"number": 15000},
  "Количество комнат": {"number": 1},
  "Площадь участка (м²)": {"number": 45},
  "Тип жилья": {"select": {"name": "Студия"}},
  "Тип аренды": {"select": {"name": "Любая"}},
  "Удобства": {
    "multi_select": [
      {"name": "Балкон"},
      {"name": "Кондиционер"},
      {"name": "Интернет"}
    ]
  },
  "Дата добавления": {"created_time": "2026-06-30T12:34:00Z"},
  "Источник объявления": {"url": "https://original-source.com/listing/123"}
}
```

#### Шаг 3: Загружаем фото в R2

```
Cloudflare R2 структура:
real-estate-propertiess/
  ├── OBJ_20260630_a4f2e1/
  │   ├── photos/
  │   │   ├── main.jpg        (главное фото)
  │   │   ├── photo_1.jpg
  │   │   ├── photo_2.jpg
  │   │   └── ...
  │   ├── videos/
  │   │   ├── vertical.mp4    (9:16)
  │   │   └── video_seedance_9x16.mp4
  │   └── metadata.json       (служебный файл)
```

**Команда загрузки:**
```bash
curl -X PUT \
  https://292befb120b8caaf4310a9cbf024756b.r2.cloudflarestorage.com/real-estate-propertiess/OBJ_20260630_a4f2e1/photos/main.jpg \
  --data-binary @photo.jpg \
  -u 'ACCESS_KEY:SECRET_KEY'

# Публичная ссылка:
# https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/OBJ_20260630_a4f2e1/photos/main.jpg
```

#### Шаг 4: Заполняем "Фото" в Notion
```json
{
  "Фото": {
    "url": "https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/OBJ_20260630_a4f2e1/photos/main.jpg"
  }
}
```

---

## [2] ОБОГАЩЕНИЕ: Гугл Карты + описания

### Процесс

#### Шаг 1: Найти место в Google Maps
```bash
# Ищем: "234 Sukhumvit Soi 15, Bangkok"
# Возвращаем: координаты и ссылку на Google Maps
# Результат URL: https://www.google.com/maps/place/13.7234,100.5682
```

**Заполняем в Notion:**
```json
{
  "Google Maps": {
    "url": "https://www.google.com/maps/place/13.7234,100.5682"
  },
  "Локация": {
    "place": {
      "address": "234 Sukhumvit Soi 15, Bangkok",
      "latitude": 13.7234,
      "longitude": 100.5682
    }
  }
}
```

#### Шаг 2: Генерируем описание для Telegram
```
🏠 Studio at Sukhumvit 45
📐 45 м²
🛏️ 1 комната
✨ Балкон, Кондиционер, Интернет
💰 15,000 ฿/месяц

Beautiful studio with balcony. Perfect for long-term rental.
Close to BTS Thonglor.

☎️ Связь: +66625124001
🔍 #OBJ_20260630_a4f2e1
```

**Заполняем в Notion:**
```json
{
  "Описание для Telegram": {
    "rich_text": [{"type": "text", "text": {"content": "🏠 Studio...\n☎️ Связь: +66625124001..."}}]
  }
}
```

#### Шаг 3: Генерируем описание для Facebook
```
Studio at Sukhumvit 45

🏠 Тип: Студия
📐 Площадь: 45 м²
💰 Цена: 15,000 батов/месяц

Beautiful studio with balcony. Perfect for long-term rental.

✨ #Балкон #Кондиционер #Интернет #Студия #Сукхумвит
Контакт: +66625124001
#OBJ_20260630_a4f2e1
```

**Заполняем в Notion:**
```json
{
  "Описание для FB Marketplace": {
    "rich_text": [{"type": "text", "text": {"content": "Studio at Sukhumvit...\n#OBJ_20260630_a4f2e1"}}]
  }
}
```

---

## [3] ВИДЕО: Рендер из фото

### Статус: `ready_for_video` → `video_in_progress`

#### Процесс
1. Берём фото из R2: `/{object_id}/photos/*.jpg`
2. Вызываем `/render` эндпоинт с параметрами:
   ```json
   {
     "object_id": "OBJ_20260630_a4f2e1",
     "photos": ["photo_1.jpg", "photo_2.jpg", ...],
     "title": "Studio at Sukhumvit 45",
     "price": "15,000 ฿/month",
     "formats": ["vertical"]
   }
   ```

3. Agent 3 (FFmpeg) + Agent 5 (Seedance) возвращают URLs:
   ```json
   {
     "vertical": "https://pub-....r2.dev/OBJ_.../videos/vertical.mp4",
     "seedance": "https://pub-....r2.dev/OBJ_.../video_seedance_9x16.mp4"
   }
   ```

4. Обновляем в Notion:
   ```json
   {
     "video_url_vertical": {"url": "https://...vertical.mp4"},
     "video_url_Seedance": {"url": "https://...seedance.mp4"},
     "Статус": {"status": {"name": "ready_to_post"}}
   }
   ```

#### При ошибке
```json
{
  "error_count": {"number": 1},
  "last_error": {"rich_text": [{"type": "text", "text": {"content": "Failed to render vertical video: timeout"}}]},
  "Статус": {"status": {"name": "video_failed"}}
}
```

→ **Алёрт в служебный чат Telegram**

---

## [4] ДИСТРИБУЦИЯ: Публикация

### Telegram канал

**Пост:**
```
🏠 Studio at Sukhumvit 45
📐 45 м²
🛏️ 1 комната
✨ Балкон, Кондиционер, Интернет
💰 15,000 ฿/месяц

Beautiful studio with balcony...

[Видео 9:16]

☎️ Связь: +66625124001
🔍 #OBJ_20260630_a4f2e1
```

**Статус:** `published`

### Facebook Marketplace

**Пост с карусельно (если несколько фото):**
```
Studio at Sukhumvit 45

[Фото 1] [Фото 2] [Фото 3] ...

🏠 Тип: Студия
📐 Площадь: 45 м²
💰 Цена: 15,000 батов/месяц

Beautiful studio...

Контакт: +66625124001
#OBJ_20260630_a4f2e1
```

### Instagram (если сделать отдельный аккаунт)
- Фото + Reel (видео 9:16)
- Bio ссылка на Lead Bot

---

## [5] КВАЛИФИКАЦИЯ: Lead Bot

### Входные каналы
- **Telegram** (комментарии, личные сообщения, replies)
- **WhatsApp** (сообщения)
- **Facebook** (комментарии, private messages)
- **Веб-форма** (на сайте, если есть)

### Логика бота

**Вопрос 1:** Какой бюджет?
```
Какой ваш бюджет аренды?
- До 10,000 ฿/месяц
- 10,000-20,000 ฿/месяц
- 20,000-50,000 ฿/месяц
- 50,000+ ฿/месяц
```

**Вопрос 2:** Где ищите?
```
Какой район предпочитаете?
- Сукхумвит
- Силом
- Асок
- Другой (напишите)
```

**Вопрос 3:** Тип сделки?
```
Долгосрочная или краткосрочная аренда?
- Долгосрочная (6+ месяцев)
- Краткосрочная (недели/месяц)
- Не важно
```

**Сроки?**
```
Когда нужно переехать?
- ASAP (срочно)
- Через 1-2 недели
- Через месяц
- Не спешу
```

### Квалификация

**Квалифицированный лид =**
- Заполнил хотя бы 3 поля
- Бюджет совпадает с имеющимися объектами
- Район совпадает

### Сохранение

```json
{
  "name": "John Doe",
  "phone": "+66812345678",
  "channel": "telegram",
  "channel_id": "john_doe_tg",
  "budget_min": 10000,
  "budget_max": 20000,
  "districts": ["Sukhumvit", "Asok"],
  "rental_type": "long_term",
  "move_in_date": "2026-07-15",
  "qualified": true,
  "matched_objects": ["OBJ_20260630_a4f2e1", "OBJ_20260629_b2d5c3"],
  "status": "ready_for_manager",
  "timestamp": "2026-06-30T15:30:00Z"
}
```

→ **CRM для лидов** (отдельная база Notion или Pipedrive)

---

## 🔄 Idempotency & Error Handling

### Правило 1: Статус — это замок
```
Если Статус = {action}_in_progress:
  При перезагрузке агент пересчитывает, не дублируя
```

### Правило 2: error_count
```
- Ошибка → error_count += 1, статус не двигается
- error_count > 3 → статус → video_failed, алёрт
- Никогда не рассчитываем дважды без явного сброса
```

### Правило 3: Дубликаты
```
Перед созданием ищем:
  SELECT * WHERE Адрес == new_address AND Дата добавления > 7 дней назад
  
Если найдён:
  Статус → duplicate
  Сохраняем ID оригинала в поле (если добавим)
```

---

## 📋 Checklist для первого запуска

- [ ] Настроить Notion API ключ
- [ ] Настроить Cloudflare R2 credentials
- [ ] Создать TG-канал для публикаций
- [ ] Создать TG-бот для квалификации
- [ ] Настроить Facebook Page + Marketplace доступ
- [ ] Настроить служебный чат для алёртов (Telegram)
- [ ] Создать `/render` сервис для видео
- [ ] Запустить парсер (шаг 1: сбор данных)

---

## 🚀 Готово к:

✅ Получению спарсенных данных в TG-бот  
✅ Структурированию в Notion  
✅ Поиску Google Maps  
✅ Генерации описаний TG/FB  
✅ Дистрибуции по каналам  
✅ Квалификации лидов  

**Следующее:** ждём первую партию спарсенных объектов! 🎯
