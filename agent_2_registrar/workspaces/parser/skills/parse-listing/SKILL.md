---
name: parse-listing
description: Parses real estate listing pages — extracts description, price, property details, and downloads photos. Use when parsing a property URL, scraping listings, or extracting listing data for the pipeline.
metadata: {"openclaw": {"requires": {"bins": ["curl"]}}}
---

# Parse Listing

## Quick start

1. Получи URL из task
2. Скачай HTML: `curl -L -A "Mozilla/5.0" "$URL"` (через `$PARSER_PROXY_URL` если задан)
3. Извлеки поля по селекторам источника (см. ниже)
4. Скачай фото в `data/listings/{listing_id}/photos/`

## Поддерживаемые источники (расширяемо)

Добавь селекторы под свои сайты в `reference.md`.

| Источник | Примечание |
|----------|------------|
| Generic | Open Graph + JSON-LD `RealEstateListing` / `Product` |
| idealista.com | og:title, og:description, gallery images |
| fotocasa.es | аналогично OG |

## Generic extraction

```bash
# Title
curl -sL "$URL" | grep -oP '(?<=<meta property="og:title" content=")[^"]*'

# JSON-LD (если есть)
curl -sL "$URL" | grep -oP '(?<=<script type="application/ld\+json">)[^<]*'
```

## Download photos

```bash
mkdir -p "data/listings/{listing_id}/photos"
# Для каждого URL изображения:
curl -L -o "data/listings/{listing_id}/photos/01.jpg" "$IMAGE_URL"
```

## Validation

Перед announce проверь:
- [ ] raw.json существует и валидный JSON
- [ ] listing_id уникален
- [ ] source_url сохранён
- [ ] Минимум title + price OR description

## Output announce

```
listing_id: {id}
title: {title}
price: {amount} {currency}
photos: {count}
path: data/listings/{id}/raw.json
```

См. [reference.md](reference.md) для селекторов по сайтам.
