# Session Handoff — Real Estate Agent

**Дата обновления:** 2026-07-06  
**Статус:** пауза — пользователь в другом проекте  
**Папка на рабочем столе:** `Агенты → Real Estate Agent → agent 4`  
**Рабочий код:** `agent 4/_import/assistant-media/`  
**НЕ трогать:** `deploy/real-estate-clawbot-20260702/` (устарел, без запроса не пересобирать)

---

## Последняя сессия (2026-07-06)

### Сделано сегодня

1. **Отдельный архив Higgsfield-агента** (для другого чата)
   - `~/Desktop/higgsfield-seedance-agent-20260706.tar.gz`
   - Исходники: `agent 4/export/higgsfield-seedance-agent/`
   - Только Seedance 2.0, без FFmpeg/Notion пайплайна

2. **Notion: колонка «Цена за месяц»**
   - Формат изменён с `baht` (฿) → `number` (только число)
   - Значения в API уже были числами — менялся только display format
   - «Цена за год» пока **baht** — не меняли (спросить если нужно)

3. **Напоминание структуры проекта** — путь `agent 4` на Desktop

### Ранее (2026-07-02) — всё ещё актуально

- Agent 3: FFmpeg reel (9:16) + Seedance **параллельно**
- `ready_to_post` сразу после reel; `video_url_Seedance` дописывается позже
- 24/7: `./start.sh` (docker: curator + chain watcher)
- Chain runner, gates, unit tests

---

## ⏳ Не сделано (продолжить при возврате)

| # | Задача |
|---|--------|
| 1 | Деплой на VPS (`./start.sh`) |
| 2 | Higgsfield OAuth на сервере |
| 3 | Credits Higgsfield (был 403) |
| 4 | Notion колонка `video_url_Seedance` (если нет в UI) |
| 5 | E2E на `20260701_001` |
| 6 | Agent 6 Publisher — боевой Publora |
| 7 | Deploy archive — не обновлять без запроса |

---

## Архитектура

```
Parser (TG) → gate 150s → Agent 2 → ready_for_video
                    ↓
          chain_runner --watch (24/7)
                    ↓
               Agent 3 (параллельно)
          FFmpeg reel    Seedance CLI
          video_vertical video_url_Seedance
                    ↓
               ready_to_post → Agent 6
```

---

## Higgsfield

| Способ | Seedance 2.0 |
|--------|--------------|
| CLI + OAuth | ✅ (VPS) |
| MCP | ✅ (Cursor) |
| API Key | ❌ |

---

## Ключевые пути

```
Desktop/Агенты/Real Estate Agent/agent 4/
├── _import/assistant-media/     ← основной пайплайн
├── export/higgsfield-seedance-agent/  ← отдельный Higgsfield-агент
└── deploy/real-estate-clawbot-20260702/  ← устарел

Desktop/higgsfield-seedance-agent-20260706.tar.gz  ← архив для другого чата
```

---

## Команды при возврате

```bash
cd "/Users/lifefmg/Desktop/Агенты/Real Estate Agent/agent 4/_import/assistant-media"

./start.sh
python3 tests/test_pipeline.py -v
python3 scripts/chain_runner.py --from-agent 3 --object-id 20260701_001
```

---

## Решения пользователя

- FFmpeg только **9:16**
- Seedance fail → всё равно `ready_to_post`
- Цена за месяц в Notion — **число без валюты**
- Higgsfield Seedance — отдельный проект/архив
- Deploy archive не обновлять без запроса

---

_При возврате: `SESSION_HANDOFF.md` → `MEMORY.md` (верх) → `CHAIN.md`_
