# Reference — Site Selectors

Добавляй селекторы под каждый источник объявлений.

## idealista.com

- Title: `h1.main-info__title` or og:title
- Price: `.info-data-price`
- Description: `.comment`
- Photos: `.gallery-container img` → data-src or src (full size)

## fotocasa.es

- Title: og:title
- Price: meta[property="product:price:amount"]
- Photos: gallery img[data-src]

## Generic JSON-LD

Ищи `@type`: `RealEstateListing`, `Apartment`, `House`, `Product`.

Поля: `name`, `description`, `offers.price`, `address`, `numberOfRooms`, `floorSize`.
