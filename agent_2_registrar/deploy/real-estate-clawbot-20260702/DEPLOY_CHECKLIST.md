# Чеклист деплоя Real Estate Agent → Clawbot

Архив: `real-estate-clawbot-20260702.tar.gz`  
Дата сборки: 2026-07-02

## Содержимое архива

```
workspace/          ← основной clawbot workspace (Agent 2 + 3)
publisher-agent/    ← Agent 4 (Notion → Publora)
.env.real-estate.example
DEPLOY_CHECKLIST.md  ← этот файл
```

---

## 1. Сервер — зависимости

```bash
# Обязательно
python3 --version    # 3.10+
node --version       # 18+
ffmpeg -version      # для Agent 3

# Python пакеты (workspace)
pip install requests

# Curator (CLIP) — отдельное venv или Docker
cd workspace
docker compose up -d curator
# Проверка: curl http://127.0.0.1:8077/health
```

---

## 2. Распаковка на сервере

```bash
cd /data/.openclaw/workspace   # или ваш путь clawbot
tar -xzf real-estate-clawbot-20260702.tar.gz

# Скопировать workspace
cp -r real-estate-clawbot-20260702/workspace/* .

# Agent 4 (рядом или в проекте)
cp -r real-estate-clawbot-20260702/publisher-agent /opt/real-estate-publisher
```

---

## 3. Секреты (НЕ из архива!)

```bash
cp .env.real-estate.example .env.real-estate
nano .env.real-estate
chmod 600 .env.real-estate
```

Заполнить:

| Переменная | Зачем |
|------------|-------|
| `NOTION_API_KEY` | Agent 2/3/4 |
| `NOTION_DB_ID` | База «Аренда недвижимости» |
| `CLOUDFLARE_*` | R2 upload фото/видео |
| `GOOGLE_MAPS_API_KEY` | Карточка проекта в Maps |
| `CONTACT_PHONE` | В подписях TG/FB |
| `PUBLORA_*` | Agent 4 (если публикуете) |

**Рекомендация:** ротировать все ключи перед продом.

---

## 4. Notion CRM — статусы

В базе `e817ce50-e788-4992-8b86-c9c9fc1fbcf7` должны быть статусы:

**Уже есть:**
- `ready_for_video`
- `video_in_progress`
- `ready_to_post`
- `video_failed`

**Добавить вручную (для Agent 4):**
- `posting_in_progress`
- `published`

Поля видео: `video_url_vertical`, `video_url_square`, `video_url_wide`

---

## 5. R2 — структура

```
music/track_001.mp3 … track_003.mp3

{YYYYMMDD_NNN}/
  photos/photo_001.jpg … index.html
  video_1x1.mp4
  video_3x4.mp4
  video_9x16.mp4
```

Публичный bucket должен отдавать `r2.dev` URL.

---

## 6. Clawbot / OpenClaw — привязка агента

1. Workspace = папка с `PLAYBOOK.md`, `AGENTS.md`, `scripts/`
2. **Отключить browser** в конфиге OpenClaw (см. `openclaw.clawbot.example.json`):

```json
"tools": { "deny": ["browser", "web_search"] }
```

3. Агент **не парсит** Airbnb — только принимает текст+фото и гоняет скрипты
4. При старте читает `BOOT.md` → `PLAYBOOK.md`

**Команды агента (или cron/shell):**

```bash
# После описания
python3 scripts/media_batch_gate.py init --session "$SESSION" --description "..."

# После каждой пачки фото
python3 scripts/media_batch_gate.py bump --session "$SESSION"

# Когда gate ready — полный цикл
./scripts/run_pipeline.sh --session "$SESSION" --source "Telegram @channel"
```

---

## 7. Проверка после деплоя

```bash
cd /data/.openclaw/workspace

# Тесты
python3 tests/test_pipeline.py -v

# Dry-run Agent 2 (нужна test session с description + photos)
python3 scripts/agent2_structurize.py --session test_session --dry-run

# Curator
curl http://127.0.0.1:8077/health

# Publora connections (если Agent 4)
python3 /opt/real-estate-publisher/scripts/list_connections.py
```

---

## 8. Первый боевой объект

```
1. Parser → описание + фото в data/sessions/{id}/
2. gate init → bump (после каждой пачки) → ready (150s idle)
3. run_pipeline.sh --session {id} --source "Airbnb URL"
4. Проверить Notion: статус ready_for_video → ready_to_post
5. Публикация:
   ./scripts/run_pipeline.sh --session {id} --from-step agent4 --publish instagram --skip-wait
```

---

## 9. One session = one object_id

Файл `data/sessions/{session}/session.json` хранит `object_id`.  
Повторный Agent 2 для той же session **обновляет** объект, не создаёт дубль.

---

## 10. Типичные проблемы

| Симптом | Решение |
|---------|---------|
| R2 400 Bad Request | Проверить `CLOUDFLARE_*` в `.env.real-estate` |
| Curator failed | `docker compose up -d curator` или fallback сработает автоматически |
| Agent 3 stuck in video_in_progress | Статус вручную → `ready_for_video`, перезапуск |
| Notion 400 на статус | Добавить option в Notion UI |
| Новый ID при re-run | Проверить `session.json` — должен быть тот же session_id |

---

## 11. Файлы для clawbot

Обязательно в workspace:
- `AGENTS.md` — инструкции агента
- `config/pipeline.json`
- `scripts/` — весь пайплайн
- `.env.real-estate` — секреты (не в git)

Документация: `FIXES.md`, `CRM_SCHEMA.md`, `TOOLS.md`
