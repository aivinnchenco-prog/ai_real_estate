# AGENTS.md — Higgsfield Seedance Video Agent

Ты агент по монтажу вертикальных роликов через **Higgsfield Seedance 2.0**.

## Задача

Из набора фото объекта недвижимости сгенерировать **9:16 видео** (Wan 2.7 @ 720p или Seedance) и загрузить в R2.

## Инвариант: монтаж только по команде

**Никогда** не запускать Wan/Seedance без **обоих** условий:

1. **«Монтаж» = ДА** в Notion (явный выбор кнопки в TG-боте или ручная правка в таблице). Пустое поле и «НЕТ» = стоп.
2. **Статус** объекта в CRM допускает монтаж (`ready_for_video` или `video_failed` для повтора).

Точка входа — **только** `chain_runner.py` (или ручной `/agent3` после проверки флагов).  
`run_from_notion.mjs` дублирует проверку `pageMontageEnabled` + статус; `--force` не обходит «Монтаж».

При интеграции нового движка видео (Wan, Seedance, …) **не** добавлять обход этих ворот.

## Не делать

- Не использовать API key для Seedance 2.0 multi-ref (не работает)
- Не блокировать работу если credits закончились — сообщить пользователю
- Не смешивать с FFmpeg reel (это отдельный агент)
- **Не менять по-шотовый промпт** (`prompt_template` в `config/seedance.json`) на «непрерывный walkthrough»: непрерывный проход заставляет Seedance выдумывать двери, комнаты и мебель. Рабочая схема — отдельный статичный шот на каждый референс + медленный dolly-in + жёсткие каты (зафиксировано 2026-07-19).

## Порядок работы

```
CRM → фото R2 → Higgsfield Seedance → hook-card → upload R2 → Notion
```

Оверлей **всегда** между Seedance и R2 (в R2 попадает видео уже с карточкой).

1. `node scripts/check_higgsfield_auth.mjs`
2. Если не authed → `higgsfield auth login`
3. `node scripts/run_from_notion.mjs --latest` или `--object-id {id}`
   - `--force` — перегенерировать
   - `--skip-overlay` — только отладка, без карточки
4. Карточка: `templates/hook_card.html` — **Тип жилья**, **Комнаты**, **Цена**, **Район**

**9 фото для Seedance:** 1-й — экстерьер/вид, max **1 кадр на локацию**, без похожих дублей.

**Отбор фото автономный** (каскад в `selectPhotosSeedance.mjs`): CLIP-куратор (если жив на 8077 / `CURATOR_BASE_URL`) → Gemini vision (`GEMINI_API_KEY`, без локальных сервисов) → fallback по имени. Куратора вручную поднимать не нужно. Проверка отбора без рендера: `node scripts/select_photos.mjs --object-id {id} [--no-curator]`.

`test_title_overlay.mjs` — отдельно, только чтобы быстро проверить шаблон на готовом видео.

## Провайдеры

| Env | Значение |
|-----|----------|
| `SEEDANCE_PROVIDER=api` | **Рекомендуется** — API Key с cloud.higgsfield.ai (DoP, без OAuth) |
| `SEEDANCE_PROVIDER=cli` | Seedance 2.0 multi-ref (нужен `higgsfield auth login`) |
| `SEEDANCE_PROVIDER=mcp` | + `HIGGSFIELD_MCP_ACCESS_TOKEN` |
| `SEEDANCE_PROVIDER=auto` | CLI → MCP → API |

**Важно:** Seedance 2.0 multi-ref (до 9 фото → **один** ролик) доступен только через OAuth (CLI/MCP). API Key — DoP, 1 фото → 1 клип.

## Конфиг

`config/seedance.json` — model, prompt, duration, max 9 images, resolution.

## Документация

- `README.md` — setup
- [Higgsfield](https://higgsfield.ai/)
- MCP URL: `https://mcp.higgsfield.ai/mcp`
