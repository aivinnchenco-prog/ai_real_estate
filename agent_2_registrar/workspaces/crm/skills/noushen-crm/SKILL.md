---
name: noushen-crm
description: Maps parsed listing data to Noushen CRM schema and syncs via API. Use when syncing listings to CRM, creating property records, or updating Noushen CRM entries.
metadata: {"openclaw": {"requires": {"bins": ["curl"], "env": ["NOUSHEN_CRM_API_URL", "NOUSHEN_CRM_API_KEY"]}}}
---

# Noushen CRM Sync

## API call

```bash
curl -X POST "${NOUSHEN_CRM_API_URL}/properties" \
  -H "Authorization: Bearer ${NOUSHEN_CRM_API_KEY}" \
  -H "Content-Type: application/json" \
  -d @data/crm/{listing_id}.payload.json
```

## Field mapping (raw.json → CRM)

| raw.json | CRM field |
|----------|-----------|
| title | title |
| description | description |
| price.amount | price |
| price.currency | currency |
| property.type | property_type |
| property.bedrooms | bedrooms |
| property.bathrooms | bathrooms |
| property.area_sqm | area_sqm |
| location.address | address |
| location.city | city |
| source_url | external_url |
| photos[] | media_urls |

## Update vs Create

1. Проверь `data/crm/{listing_id}.json` — если есть `crm_id`, используй PUT
2. Иначе POST → сохрани `crm_id` из ответа

## Payload template

Создай `data/crm/{listing_id}.payload.json` перед запросом:

```json
{
  "title": "...",
  "description": "...",
  "price": 250000,
  "currency": "EUR",
  "property_type": "apartment",
  "bedrooms": 3,
  "bathrooms": 2,
  "area_sqm": 95,
  "address": "...",
  "city": "...",
  "external_url": "...",
  "media_urls": ["https://..."]
}
```

> **Note:** Замени endpoint и поля под реальный API Noushen CRM когда получишь документацию.

## Validation

- [ ] raw.json прочитан
- [ ] crm_id получен или обновлён
- [ ] data/crm/{listing_id}.json сохранён
