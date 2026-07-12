# INPUT FORMAT — Как отправить спарсенные данные

## 📨 Где отправлять

Когда парсер соберёт информацию об объекте, отправь её в **Telegram ботом** в формате JSON или структурированным текстом.

## 🔄 Формат #1: JSON (предпочтительно)

```json
{
  "title": "Studio at Sukhumvit 45",
  "address": "234 Sukhumvit Soi 15, Bangkok",
  "district": "Sukhumvit",
  "housing_type": "Studio",
  "rooms": 1,
  "area": 45.0,
  "price_monthly": 15000,
  "price_yearly": null,
  "floor": 5,
  "year_built": 2015,
  "description": "Beautiful studio with balcony. Perfect for long-term rental. Close to BTS Thonglor.",
  "amenities": ["Balcony", "Air Conditioner", "Internet", "Parking"],
  "view": "City",
  "rental_type": "Long-term",
  "available_from": "2026-07-15",
  "pets_allowed": false,
  "photos": [
    "https://example.com/photo1.jpg",
    "https://example.com/photo2.jpg",
    "https://example.com/photo3.jpg"
  ],
  "source_url": "https://original-listing-site.com/listing/12345",
  "owner_phone": "+6681234567",
  "owner_name": "Mr. Somchai"
}
```

## 🔄 Формат #2: Структурированный текст

```
🏠 ОБЪЕКТ
─────────────────────
Название: Studio at Sukhumvit 45
Адрес: 234 Sukhumvit Soi 15, Bangkok
Район: Sukhumvit

📐 ХАРАКТЕРИСТИКИ
─────────────────────
Тип жилья: Студия
Комнаты: 1
Площадь: 45 м²
Этаж: 5
Год постройки: 2015

💰 ЦЕНА
─────────────────────
Месячная: 15,000 ฿
Годовая: (не указано)

📅 УСЛОВИЯ
─────────────────────
Тип аренды: Долгосрочная
Доступно с: 2026-07-15
Питомцы: Не разрешены

✨ УДОБСТВА
─────────────────────
• Балкон
• Кондиционер
• Интернет
• Парковка

📝 ОПИСАНИЕ
─────────────────────
Beautiful studio with balcony. Perfect for long-term rental. 
Close to BTS Thonglor.

🖼️ ФОТО (прямые ссылки)
─────────────────────
https://example.com/photo1.jpg
https://example.com/photo2.jpg
https://example.com/photo3.jpg

📞 КОНТАКТЫ
─────────────────────
Владелец: Mr. Somchai
Телефон: +6681234567

🔗 ИСТОЧНИК
─────────────────────
https://original-listing-site.com/listing/12345
```

## 🔄 Формат #3: Простой текст (minimum)

```
Название: Studio at Sukhumvit 45
Адрес: 234 Sukhumvit Soi 15, Bangkok
Район: Sukhumvit
Комнаты: 1
Площадь: 45 м²
Цена месячная: 15,000 ฿
Описание: Beautiful studio with balcony. Perfect for long-term rental. Close to BTS Thonglor.
Фото: https://example.com/photo1.jpg, https://example.com/photo2.jpg, https://example.com/photo3.jpg
Контакт: +6681234567
Источник: https://original-listing-site.com/listing/12345
```

---

## ⚠️ Обязательные поля

Минимум для обработки:
- ✅ **Название объекта**
- ✅ **Адрес** (для Google Maps)
- ✅ **Описание**
- ✅ **Фото** (минимум 1)

Остальное (цена, комнаты, удобства) — желательно, но система заполнит пробелы.

---

## 🎯 Дополнительные поля (если есть)

```json
{
  "deposit": 30000,
  "furnished": true,
  "water_included": false,
  "electricity_included": false,
  "wifi_included": true,
  "utility_estimate_monthly": 2500,
  "contract_terms": "flexible",
  "minimum_rental_period": 1,
  "maximum_rental_period": null,
  "cancellation_policy": "1 month notice",
  "cleaning_fee": 5000
}
```

---

## 📤 Как я обработаю

### Шаг 1: Парсинг (1 минута)
Я прочитаю данные, извлеку ключевое, сгенерирую Object ID.

### Шаг 2: Структурирование (2 минуты)
Создам запись в Notion с заполненными полями.

### Шаг 3: Обогащение (3 минуты)
- Найду место в Google Maps
- Загружу фото в Cloudflare R2
- Сгенерирую описания для TG и Facebook
- Обновлю статус → `ready_for_video`

### Шаг 4: Видео (5-10 минут)
Вызову `/render` для создания видео во всех форматах.

### Шаг 5: Дистрибуция (2 минуты)
Опубликую в TG-канал и Facebook Marketplace.

**Итого: ~20 минут на объект** (в параллели может быть 5-10 объектов одновременно)

---

## 📊 Пример полного цикла

```
[15:00] Отправил JSON объекта в TG-бот
         ↓
[15:02] Парсинг завершён → Object ID: OBJ_20260630_a4f2e1
        Notion запись создана (статус: ready_for_video)
         ↓
[15:05] Фото загружены в R2
        Google Maps найдена
        Описания сгенерированы
         ↓
[15:10] Видео готово (вертикальное, квадратное, широкое)
        Статус → ready_to_post
         ↓
[15:12] Пост в Telegram опубликован
        Пост в Facebook Marketplace опубликован
        Статус → published
         ↓
[Постоянно] Lead Bot слушает replies
         ↓
[Когда квалифицирован] Лид → CRM на закрытие живому менеджеру
```

---

## 🔒 Безопасность

- ✅ Все данные хранятся зашифрованными в Notion
- ✅ Фото/видео хранятся в приватной папке R2
- ✅ Публичные ссылки только на необходимых объектах
- ✅ API ключи в переменных окружения, не в коде
- ✅ Все операции логируются с timestamp и Object ID

---

## 📞 Если ошибка

Если обработка объекта упадёт:
1. В поле `last_error` запишется текст ошибки
2. `error_count` увеличится на 1
3. Статус останется прежним (не двинется вперёд)
4. Алёрт придёт в служебный чат Telegram
5. Я могу пересчитать через `error_count := 0` + ручной ретрай

---

## ✅ Готово!

Жду первых спарсенных объектов. Отправь в TG-бот любой формат из трёх выше — я разберусь и обработаю. 🚀
