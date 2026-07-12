# MEMORY — Agent 5 (Higgsfield Seedance)

Обновлено: 2026-07-07

## Пайплайн (основной)

```
CRM (Notion) → R2 photos → Higgsfield Seedance → hook-card оверлей → R2 → Notion video_url_Seedance
```

**Команда:**
```bash
node scripts/check_higgsfield_auth.mjs          # проверка сессии
higgsfield auth login                           # если истекла (~30 сек в браузере)
node scripts/run_from_notion.mjs --latest       # последний объект с фото в R2
node scripts/run_from_notion.mjs --object-id ID --force
```

**Только оверлей на готовое видео (без Seedance, без auth):**
```bash
node scripts/test_title_overlay.mjs --object-id ID --publish
```

## Параметры Seedance (`config/seedance.json`)

| Параметр | Значение |
|----------|----------|
| Модель | `seedance_2_0` |
| Провайдер | CLI OAuth (`SEEDANCE_PROVIDER=cli`) |
| Фото | 9 (макс. за запрос) |
| Длительность | 14 сек |
| Разрешение | 480p, 9:16 |
| Склейка | нет — один ролик |
| Звук | выкл (`generate_audio: false`) |
| Промпт | плавная стабилизированная проходка камеры вперёд |

## Hook-card оверлей

- Шаблон: `templates/hook_card.html` (из Downloads `hook_card.html`)
- Рендер: Playwright → прозрачный PNG 1080×1920 → ffmpeg scale под видео → overlay
- Валюта: **฿** (THB), не $
- В R2 попадает **только видео с карточкой** (между Seedance и upload)
- `--skip-overlay` — только отладка

### CRM → шаблон

| Плейсхолдер | Поле Notion |
|-------------|-------------|
| `{{TITLE_PHRASE}}` | Тип жилья |
| `{{BEDROOMS}}` | Количество комнат |
| `{{PRICE}}` / `{{PERIOD}}` | Цена за месяц (fallback: за год) |
| `{{DISTRICT}}` | Район |

Конфиг полей: `config/notion.json` → `overlay_fields`

## Отбор 9 фото (`selectPhotosSeedance.mjs`)

1. **Первый кадр** — экстерьер/вид по имени файла, иначе `photo_001`
2. **Max 1 фото на локацию** (`max_per_category: 1`)
3. **Разнос по альбому** — MMR-lite по номерам, без дублей одной комнаты
4. Curator `http://127.0.0.1:8077/select-diverse` если онлайн, иначе fallback

## Higgsfield auth

- Токены **короткоживущие** — это норма CLI, вечной сессии нет
- При `Session expired` → `higgsfield auth login`
- API Key (cloud.higgsfield.ai) **не** даёт Seedance 2.0 multi-ref — только OAuth CLI/MCP
- Аккаунт: `ai.vinnchenco@gmail.com`, workspace Private

## Notion CRM

- DB: `e817ce50e78849928b86c9c9fc1fbcf7` («Аренда недвижимости»)
- Колонка видео: `video_url_Seedance`
- Проверенный объект: `20260702_001` (TITLE LEGENDARY 2BR, Bang Tao)

## R2

- Bucket: `real-estate-propertiess`
- Public: `https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev`
- Видео: `{object_id}/video_seedance_9x16.mp4`

## Ключевые файлы

| Файл | Назначение |
|------|------------|
| `scripts/run_from_notion.mjs` | главный entry |
| `renderSeedance.mjs` | Seedance → overlay → R2 |
| `applyTitleOverlay.mjs` | hook-card (Playwright + ffmpeg) |
| `selectPhotosSeedance.mjs` | отбор 9 разнообразных фото |
| `notionCrm.mjs` | Notion read/write + overlay meta |
| `scripts/test_title_overlay.mjs` | оверлей без Seedance |
| `scripts/check_higgsfield_auth.mjs` | preflight auth |

## Баги исправленные в сессии

1. Оверлей не виден — PNG 1080×1920 без scale на видео 496×864; карточка была за кадром → fix: `scale` overlay под размер видео
2. Multi-batch concat убран — один ролик из 9 фото
3. `extractVideoUrl` в CLI — брал PNG вместо mp4

## Завтра / next steps

- [ ] `higgsfield auth login` перед новой генерацией
- [ ] Полный прогон: `run_from_notion.mjs --latest --force` с новым отбором фото + оверлеем ฿
- [ ] Опционально: поднять curator на `:8077` для CLIP-отбора
- [ ] Опционально: 720p если качество 480p мало
