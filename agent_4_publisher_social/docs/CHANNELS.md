# Каналы публикации

## TikTok — готово ✅

Пакет: `com.ss.android.ugc.trill`

Флоу:
1. Главная → **Создать**
2. **Upload** (`upload_hot_area`)
3. Альбом **`publisher_social`**
4. Вкладка **Видео** → первое видео
5. **Далее** (галерея) → **Далее** (редактор)
6. Описание (`caption_social`)
7. Локация **Пхукет** (`config/publisher.json` → `tiktok.location`)
8. **Опубликовать** — только с `--live`  
   Без `--live` (`--ui`) — стоп / черновик

```bash
export PATH="$HOME/bin:$PATH"
export PYTHONPATH=src
python3 -m publisher_social publish --page-id PAGE_ID --channel tiktok --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel tiktok --live
```

## TikTok Carousel (фото) — код готов

Тот же пакет `com.ss.android.ugc.trill`, но photo post вместо видео.

Флоу:
1. Создать → Upload → альбом `publisher_social`
2. Вкладка **Фото** → несколько изображений (до `tiktok.carousel.max_images`)
3. Далее → (редактор при наличии) → caption → локация **Пхукет**
4. `--ui` → Черновики · `--live` → Опубликовать

Нужны ≥2 фото на телефоне (`prepare --push-media`).

```bash
python3 -m publisher_social publish --page-id PAGE_ID --channel tiktok_carousel --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel tiktok_carousel --live
```

## Instagram Reel — готово ✅

Пакет: `com.instagram.android` (проверено 438.0.0)

Флоу:
1. **Создать видео Reels** (черновик → **Начать новое видео**)
2. **Галерея** → фильтр **Видео** → ролик
3. **Далее** → **Далее**
4. Подпись (`caption_social` + CTA)
5. **Добавить место** → **Phuket, Thailand** (обязательно)
6. **Поделиться также:** Threads ON + Facebook/OpenHome ON  
   Аудитория: **Все / общедоступный**
7. `--ui` → Черновики · `--live` → Далее → **Поделиться**

```bash
python3 -m publisher_social publish --page-id PAGE_ID --channel instagram_reel --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel instagram_reel --live
```

## Instagram Carousel — готово (калибровка на телефоне)

1. Лента → create (верх слева) → **ПУБЛИКАЦИЯ**
2. **Выбрать несколько** → альбом `publisher_social` → фото
3. Далее → подпись → **Phuket, Thailand**
4. Threads/Facebook если есть на экране; Share = **Поделиться**

```bash
python3 -m publisher_social publish --page-id PAGE_ID --channel instagram_carousel --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel instagram_carousel --live
```

## FB Marketplace — код готов (телефон)

Пакет: `com.facebook.katana`

Порядок: в `publish-all` / очереди Marketplace идёт **последним** (`orchestration.final_channels`), после остальных соцсетей.

Важно:
- Marketplace **не работает со страницы Page** — нужен **личный профиль**
- У новых продавцов Facebook часто ставит **лимит объявлений** («Достигнуто ограничение») — тогда форма без типа/цены; нужно подождать
- Фото **только** из Notion **`brand_open_home_url`** (не колонка «Фото», не carousel). Альбом на телефоне: `brand_open_home`
- Ссылку на объявление телефон не копирует; URL позже поступит через API.

Флоу:
1. `fb://marketplace` → **Продать**
2. **Продажа и аренда недвижимости**
3. **Сдается в аренду** · тип (Дом/Квартира/Таунхаус) · спальни · санузлы · цена/мес
4. Описание (`caption_fb`) · фото из альбома `brand_open_home`
5. **Далее** → экран групп → **Опубликовать** только с `--live`  
   `--ui` — стоп / черновик

```bash
python3 -m publisher_social publish --page-id PAGE_ID --channel fb_marketplace --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel fb_marketplace --live
```

### FB Marketplace automation (phone only)

Только канал `fb_marketplace` использует state machine + semantic validation:

- UI tree (`dump_hierarchy`) — основной источник; snapshot обновляется после каждого перехода
- Селекторы — `config/marketplace_selectors.json` (RU/EN aliases, без правок Python)
- Пороги и retries — `config/marketplace_ui.json`
- Vision — fallback, **выключен по умолчанию** (`vision.enabled=false`)
- Coordinate clicks — последний fallback, **выключен по умолчанию**
- Publish — только после `final_content_validation`
- Unknown UI / checkpoint / low confidence → **safe stop** (`needs_review`, `blocked_checkpoint`, `validation_failed`)
- Diagnostics — `diagnostics/<object_id>/<timestamp>/` (`screenshot.png`, `ui_dump.xml`, `context.json`, `actions.jsonl`)

Resume после `needs_review`:

```bash
PYTHONPATH=src python3 scripts/resume_mp_description.py --page-id PAGE_ID --live
PYTHONPATH=src python3 scripts/resume_mp_location.py --page-id PAGE_ID --live
```

Staging: включить vision только в `publisher.json` → `marketplace_ui.vision.enabled=true` (provider `fake` для offline tests).

## Twitter / X — код готов

Пакет: `com.twitter.android`

По умолчанию `twitter.media=video`. Для фото: `"media": "images"` (макс 4).

Флоу (видео):
1. FAB **Опубликовать пост**
2. **Фотографии** → Подборки → На этом устройстве → `publisher_social`
3. Ячейка с **Длительность** → **Готово**
4. Текст из **Описание X.com** (обрезка `max_chars`, по умолчанию 280; если колонка пустая — «Описание соц.сети»)
5. Опционально место (Phuket / Amphoe Thalang)
6. `--ui` стоп · `--live` → **Опубликовать пост**
7. После `--live` сценарий завершается сразу после принятия публикации. URL позже поступит через API.

```bash
python3 -m publisher_social publish --page-id PAGE_ID --channel twitter --ui
python3 -m publisher_social publish-retry --page-id PAGE_ID --channel twitter --live
```

## LinkedIn — код готов

Пакет: `com.linkedin.android`

Флоу:
1. **Создать публикацию**
2. **Фото** → Подборки → На этом устройстве → `publisher_social`
3. Выбор фото → **Готово** → **Далее**
4. Текст из **Описание соц.сети** (`caption_social`)
5. Аудитория **Общедоступно**
6. `--ui` стоп · `--live` → **Разместить**

```bash
python3 -m publisher_social publish --page-id PAGE_ID --channel linkedin --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel linkedin --live
```

## FB Groups — готово ✅

Список групп: `config/fb_groups_list.txt` (share-ссылка или `/groups/ID/`).  
Текст: **Описание для FB Marketplace** (`caption_fb`).  
Лимит за объект: `fb_groups.max_groups_per_object` (сейчас 2).

Два UI:
1. **Discussion** (напр. «Аренда жилья Пхукет») — Напишите что-нибудь → Галерея → multi-select (кнопка или long-press на slide_01) → caption → место **Amphoe Thalang** → возврат на композер → **Опубликовать** (проверка: кнопка исчезла после тапа)
2. **Property/sell** (напр. PHUKET-RENT-PROPERTY) — «Что вы продаете?» → форма аренды (как Marketplace)

`--ui` — стоп перед Опубликовать · `--live` — пост

```bash
python3 -m publisher_social publish --page-id PAGE_ID --channel fb_groups --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel fb_groups --live
```

## YouTube Shorts — готово ✅ (генеральный live-тест пройден)

Пакет: `com.google.android.youtube`  
Медиа: `video_url_Seedance` → альбом `publisher_social`  
Ссылка после поста: позже через API в Notion **`post_url_youtube`**

**Название Shorts** (авто из Notion):
`Аренда вилла 3BR Phuket Thalang`  
← **Тип жилья**, **Количество комнат**, `Phuket`, **Район**  
(или колонка `Название YouTube Shorts`, если заполнена).

**Описание** (обязательно): колонка **`Описание соц.сети`**.

Линейный флоу (без лишних экранов):
1. **+** / Создание видео → Shorts → галерея  
2. **Самое новое видео** (верхний-левый thumbnail в сетке)  
3. **Готово**  
4. **Далее**  
5. Название  
6. **Развернуть**  
7. **Добавить описание**  
8. **Местоположение** → `Amphoe Thalang` / Phuket  
9. **Укажите аудиторию** → **Видео не для детей**  
10. **Загрузить** → завершить Android-сценарий; ссылку не копировать.

```bash
export PATH="$HOME/bin:$PATH"
export PYTHONPATH=src
python3 -m publisher_social publish --page-id PAGE_ID --channel youtube_shorts --ui
python3 -m publisher_social publish --page-id PAGE_ID --channel youtube_shorts --live
```
