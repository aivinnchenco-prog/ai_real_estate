# AGENTS.md

## Первое действие

Прочитай **`PLAYBOOK.md`** и выполни его **без отклонений**.

Ты не парсер. Ты не используешь браузер для объявлений. Ты запускаешь скрипты.

---

## Роли в пайплайне

См. **`CHAIN.md`** — цепочка по статусам Notion.

| Этап | Скрипт | Условие в Notion |
|------|--------|------------------|
| Gate + Agent 2 | `run_pipeline.sh` / `agent2_structurize.py` | session ready |
| Agent 3 | `chain_runner.py` → `run_from_notion.mjs` (Wan / Seedance) | `ready_for_video` + галерея + **«Монтаж» = ДА** |
| Agent 6 | `chain_runner.py` → `publish_pipeline.py` | `ready_to_post` + video URL |

Agent 3: FFmpeg reel и Seedance **параллельно**; `ready_to_post` ставится сразу после reel.

**Не запускай Agent 3/6 вручную без chain_runner** — он проверяет CRM.

**Монтаж (Wan/Seedance):** только после кнопки «Монтаж: ДА» в боте. Новый объект в Notion создаётся с `Монтаж=НЕТ`; пустое поле ≠ разрешение. Не вызывать `run_from_notion.mjs` / `wan_reel.py` из Агента 2 или сразу после structurize.

---

## One session = one object_id

`data/sessions/{session}/session.json` — повторный запуск **обновляет** объект, новый ID не создаётся.

---

## Запрещено

- Браузер / скрейпинг Airbnb, Booking, FB
- Выдумывать поля объекта
- Agent 3 до `READY_FOR_VIDEO`
- Agent 6 до `ready_to_post`
- Фразы из раздела «Запрещённые фразы» в `PLAYBOOK.md`

---

## VPS 24/7 (встроено)

Сервисы часть проекта — не отдельный деплой:

```bash
./start.sh                      # docker compose up (curator + chain)
./scripts/healthcheck_vps.sh
```

При `run_pipeline.sh` сервисы поднимаются автоматически (`ensure_services.sh`).

Инструкция OAuth Seedance: **`deploy/VPS_24X7.md`**

---

## Документация

- `PLAYBOOK.md` — **главный алгоритм**
- `INBOUND.md` — краткая шпаргалка для Telegram
- `TOOLS.md` — whitelist команд
- `deploy/VPS_24X7.md` — автозапуск на сервере
- `CRM_SCHEMA.md`, `config/pipeline.json`
