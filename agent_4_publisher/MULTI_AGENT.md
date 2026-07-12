# Agent 6 — Publisher (часть Real Estate Multi-Agent)

Этот архив — **один агент** из мультиагентной системы недвижимости.  
Родительская папка: `Desktop/Агенты/Real Estate Agent/agent_6_piblich_social`

> **Ранее** этот агент был **Agent 4** в монорепо `agent 4`. После добавления Agent 5 (Seedance) публикация переехала на **Agent 6**.

---

## Место в системе

```
┌─────────────────────────────────────────────────────────────┐
│           Real Estate Multi-Agent System                    │
├─────────────────────────────────────────────────────────────┤
│  Agent 1  Parser (Telegram)     — описание + фото           │
│  Agent 2  Strukturator            — R2 + Notion CRM         │
│  Agent 3  Video Creator           — FFmpeg reel               │
│  Agent 5  Seedance (Higgsfield)   — video_url_Seedance      │
│  ► Agent 6  Publisher ◄           — Notion → Metricool ← ВЫ │
│  Agent 7  Qualifier (planned)     — лиды TG/WhatsApp        │
└─────────────────────────────────────────────────────────────┘
```

**Ты — Agent 6.** Твоя зона ответственности: публикация готовых объектов в соцсети.

Ты **не** парсишь объявления, **не** структурируешь CRM, **не** монтируешь видео.  
Ты берёшь запись из Notion со статусом `ready_to_post` и публикуешь через [Metricool](https://app.metricool.com/).

---

## Вход (от Agent 3, опционально Agent 5)

| Поле Notion | Откуда |
|-------------|--------|
| `Статус` = `ready_to_post` | Agent 3 завершил FFmpeg reel |
| `video_url_vertical` | FFmpeg reel 9:16 (обязательно для Reels/TikTok) |
| `video_url_Seedance` | Agent 5 — Higgsfield Seedance (опционально) |
| `Описание для Telegram` | Agent 2 — подпись к посту |
| `Описание для FB Marketplace` | Agent 2 — для Facebook |

## Выход

| Поле / статус | Значение |
|---------------|----------|
| `metricool_post_id` | ID поста в Metricool |
| `Статус` → `ready_to_post` + `agent6_locked=true` | После успеха Agent 6 |
| `last_error` | При ошибке |

---

## Связь с родительским пайплайном

В монорепо (`agent 4/_import/assistant-media/`) Agent 6 вызывается через:

```bash
python3 scripts/chain_runner.py --from-agent 6 --object-id 20260701_001 --publish instagram
```

`chain_runner.py` ищет этот скрипт:
- `agent_6_piblich_social/real-estate-agent6-publisher/scripts/publish_pipeline.py` (standalone)
- `workspaces/publisher/scripts/publish_pipeline.py` (legacy в монорепо)
- или путь из `config/pipeline.json` → `chain.publisher_script`

**Этот архив** — standalone-копия для отдельной разработки/тестов.

---

## Notion CRM

База: [Аренда недвижимости](https://app.notion.com/p/e817ce50e78849928b86c9c9fc1fbcf7)  
`NOTION_DB_ID=e817ce50-e788-4992-8b86-c9c9fc1fbcf7`

---

## Соседние агенты (отдельные архивы)

| Агент | Папка |
|-------|-------|
| Agent 3 Video (FFmpeg) | `agent 4/workspaces/video/` |
| Agent 5 Seedance | `agent_5_higgsfield_video/` |
| Agent 6 Publisher | `agent_6_piblich_social/` (этот пакет) |
| Agent 7 Qualifier | `agent_7_qualifying/` |
| Полный пайплайн | `agent 4/_import/assistant-media/` |

---

## Что доработать в этом агенте

- [ ] Боевой E2E с реальным Metricool API token
- [ ] Статусы `video_in_progress` / `ready_to_post` + `agent6_locked` в Notion UI
- [ ] Facebook Marketplace (отдельный flow)

---

_Для AI в новом чате: начни с `AGENTS.md` + `README.md` + этот файл._
