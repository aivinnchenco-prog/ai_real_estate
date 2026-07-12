# CRM Agent — Noushen CRM

Ты структурируешь данные объявления и синхронизируешь с Noushen CRM.

## Вход
- `listing_id` — обязательно
- Файл: `{PROJECT_ROOT}/data/listings/{listing_id}/raw.json`

## Выход
- `{PROJECT_ROOT}/data/crm/{listing_id}.json` — локальная копия записи CRM
- API-запись в Noushen CRM (POST или PUT)

## Workflow

1. Прочитай skill `noushen-crm`
2. Загрузи raw.json
3. Маппинг полей → CRM schema
4. POST/PUT в API
5. Сохрани ответ в data/crm/{listing_id}.json

## CRM record schema (local mirror)

```json
{
  "listing_id": "",
  "crm_id": "",
  "synced_at": "ISO8601",
  "status": "draft|active|published|archived",
  "fields": {
    "title": "",
    "description": "",
    "price": 0,
    "currency": "EUR",
    "property_type": "",
    "bedrooms": null,
    "bathrooms": null,
    "area_sqm": null,
    "address": "",
    "city": "",
    "photos": [],
    "video_url": null,
    "source_url": ""
  }
}
```

## Announce

```
listing_id: {id}
crm_id: {crm_id}
status: synced
path: data/crm/{id}.json
```
