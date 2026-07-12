# Agent 2 (Strukturator) — Checklist для каждого объекта

## Обязательные поля при создании Notion записи

### ✅ Основные поля
- [ ] **Название объекта** — полное имя (Skypark Celeste 4612)
- [ ] **Объект ID** — YYYYMMDD_NNN (напр. `20260701_001`)
- [ ] **Адрес** — полный адрес (Choeng Thale, Phuket, Thailand)
- [ ] **Статус** — ready_for_video

### ⚠️ ВАЖНО: Дополнительные поля (часто пропускаются!)

- [ ] **Google Maps** — URL с адресом
  ```
  https://www.google.com/maps/search/{ADDRESS_URL_ENCODED}
  Пример: https://www.google.com/maps/search/Choeng%20Thale%2C%20Phuket%2C%20Thailand
  ```

- [ ] **Источник объявления** — откуда взято объявление
  ```
  Примеры:
  - Telegram (@Vinchenco_B)
  - Airbnb (https://airbnb.ru/rooms/1651997498952000693)
  - Facebook (ссылка на объявление)
  - Email (от кого)
  ```

### Формат при создании в Python

```python
properties = {
    "Название объекта": NotionCRM.build_title("Skypark Celeste 4612"),
    "Объект ID": NotionCRM.build_text("20260701_001"),
    "Адрес": NotionCRM.build_text("Choeng Thale, Phuket, Thailand"),
    "Google Maps": NotionCRM.build_url(f"https://www.google.com/maps/search/{quote('Choeng Thale, Phuket, Thailand')}"),
    "Источник объявления": NotionCRM.build_text("Telegram (@Vinchenco_B)"),
    "Статус": NotionCRM.build_status("ready_for_video"),
}

crm = NotionCRM(api_key, db_id)
page = crm.create_page(properties)
```

---

## Почему это важно?

1. **Google Maps** — позволяет быстро найти объект на карте, проверить район
2. **Источник объявления** — отслеживание, откуда приходят данные, контакт для вопросов

---

## Автоматизация в будущем

Когда будут входящие боты (TG, Airbnb parser):
- Bot автоматически указывает `source` (Telegram, Airbnb URL, etc.)
- Agent 2 извлекает адрес и генерирует Google Maps ссылку
- Оба поля заполняются на этапе create_page()
