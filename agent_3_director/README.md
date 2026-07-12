# Higgsfield Seedance Agent — монтаж ролика 9:16

**Agent 5** в пайплайне Real Estate Agent.  
Генерация видео через [Higgsfield Seedance 2.0](https://higgsfield.ai/) — multi-reference image-to-video.

Notion CRM → R2 photos → **один** ролик 9:16 (до 9 фото, 14 сек) → R2 → `video_url_Seedance` в Notion.

---

## Быстрый старт

```bash
# 1. Зависимости
npm install
curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh

# 2. Секреты
cp .env.example .env
nano .env

# 3. OAuth (обязательно для Seedance 2.0 multi-ref)
higgsfield auth login

# 4. Проверки
node scripts/test_higgsfield_providers.mjs

# 5. Генерация из CRM (последний объект с фото в R2)
npm run video
# или: node scripts/run_from_notion.mjs --latest
# dry-run: npm run video:dry
# конкретный объект: node scripts/run_from_notion.mjs --object-id 20260702_001
# перегенерация: node scripts/run_from_notion.mjs --latest --force
```

---

## Провайдеры (приоритет `auto`)

| # | Способ | Seedance 2.0 | Где |
|---|--------|--------------|-----|
| 1 | **CLI** | ✅ | VPS, сервер, терминал |
| 2 | **MCP** | ✅ | Cursor (`config/higgsfield-mcp.cursor.example.json`) |
| 3 | **API Key** | ❌ | Только DoP, не multi-ref |

Документация: [higgsfield.ai](https://higgsfield.ai/) → MCP & CLI

---

## Структура

```
higgsfield-seedance-agent/
├── renderSeedance.mjs      # главный рендер (до 9 фото → 1 ролик)
├── higgsfieldCli.mjs       # CLI: seedance_2_0
├── higgsfieldMcp.mjs       # MCP client
├── higgsfieldClient.mjs    # REST fallback
├── r2util.mjs              # upload в Cloudflare R2
├── config/seedance.json    # параметры модели
├── scripts/run_from_notion.mjs  # основной entry: Notion CRM → R2 → Seedance
├── scripts/run_seedance.mjs     # ручной запуск по object-id + r2-keys
├── MEMORY.md                    # контекст сессии / память проекта
├── curator/curator_diverse.py  # MMR логика (unit test)
└── AGENTS.md               # инструкции для AI-агента
```

---

## Параметры (`config/seedance.json`)

- **9 фото** — diverse selection (через curator `/select-diverse`)
- **9:16**, 480p/720p, **14 сек**, один ролик
- До **9 ref-фото** за один запрос Seedance (без склейки)
- Output: `{object_id}/video_seedance_9x16.mp4` в R2

---

## Headless VPS

```bash
# Mac: SSH tunnel
ssh -L 3843:127.0.0.1:3843 user@vps

# VPS
./scripts/higgsfield_auth_remote.sh
# или: higgsfield auth login --port 3843
```

Нужен **баланс credits** на higgsfield.ai (иначе 403).

---

## Тесты

```bash
node scripts/test_seedance_unit.mjs
python3 tests/test_curator_diverse.py
node scripts/test_higgsfield_auth.mjs      # API key → motions only
node scripts/test_higgsfield_providers.mjs # какой provider выберется
```

---

## Проверенные параметры (2026-07-06)

| Параметр | Значение |
|----------|----------|
| Модель | `seedance_2_0` |
| Провайдер | CLI OAuth |
| Фото | 9 (макс. за запрос) |
| Длительность | 14 сек |
| Формат | 9:16, 480p |
| Склейка | нет — один ролик |
| Промпт | плавная стабилизированная проходка камеры вперёд |

Пример: `20260702_001` → `video_seedance_9x16.mp4` в R2.

---

## Связь с Real Estate Agent

Agent 5 работает параллельно с FFmpeg reel (Agent 3).  
Источник фото и метаданных — Notion CRM + Cloudflare R2.
