# Metricool MCP — Cursor, OpenClaw, Clawbot, Hermes

Официальный MCP-сервер Metricool: [github.com/metricool/mcp-metricool](https://github.com/metricool/mcp-metricool)  
Обзор от Metricool: [metricool.com/metricool-mcp-claude](https://metricool.com/metricool-mcp-claude/)

## Зачем MCP, если уже есть REST API?

| Слой | Технология | Когда |
|------|------------|-------|
| **Продакшн-конвейер** | Python-скрипты (`publish_pipeline.py`, `publish_telegram.py`) | Cron, webhook Agent 5, lock/quota, запись в Notion |
| **Оркестрация агентов** | MCP-инструменты | Clawbot / OpenClaw / Hermes — coordinator вызывает Metricool как tool |
| **Ручная работа** | MCP в Cursor | Аналитика, отладка, разовые посты из чата |

**Да — для связки всех агентов на Clawbot/OpenClaw/Hermes MCP — правильный слой интеграции.**  
Агент-coordinator не должен знать детали REST Metricool: он вызывает tool `schedule_post` / `get_instagram_reels` и т.д.

Скрипты остаются для **детерминированных** шагов (idempotency, `agent6_locked`, лимит 3 поста/сеть). Coordinator может:
- вызывать `exec python3 scripts/publish_pipeline.py ...` для боевого постинга, **или**
- вызывать MCP tools для аналитики и ad-hoc задач.

Оба варианта используют **одни и те же** ключи из `.env`.

---

## Требования

- План Metricool **Advanced** или **Custom** (API + MCP)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — для запуска `uvx mcp-metricool`
- Переменные в `.env`:
  - `METRICOOL_USER_TOKEN` — Account settings → API → userToken
  - `METRICOOL_USER_ID` — из URL аккаунта
  - `METRICOOL_BLOG_ID` — из URL бренда (передаётся в каждый вызов tool, как в REST API)

---

## 1. Cursor — уже настроено

MCP установлен автоматически:

| Что | Где |
|-----|-----|
| Конфиг Cursor | `/.cursor/mcp.json` (корень workspace) |
| Ключи Metricool | `real-estate-agent6-publisher/.env` |
| `uvx` | `~/.local/bin/uvx` (установлен) |

Серверы в конфиге:
- **metricool** — читает `.env` через `envFile`
- **chatplace** — выключите в Customize, пока нет `CHATPLACE_API_KEY`

### Что сделать вам (2 шага)

1. Заполните в `real-estate-agent6-publisher/.env`:
   ```
   METRICOOL_USER_TOKEN=...
   METRICOOL_USER_ID=...
   METRICOOL_BLOG_ID=...
   ```
2. **Перезапустите Cursor** (Cmd+Shift+P → «Reload Window»)

Проверка: **Customize → MCP** — `metricool` должен быть зелёным.  
В чате: «Покажи профили Metricool для blogId …»

Если `metricool` красный — откройте **Output → MCP Logs**.

### ChatPlace в Cursor (пока выключен)

Пока ключ пустой: **Customize → MCP → chatplace → toggle OFF**.  
После тестов: добавьте `CHATPLACE_API_KEY` в `.env` и включите сервер.

Подробнее: `skills/chatplace-funnel/SKILL.md`, `config/openclaw.mcp-chatplace.example.json`

---

## 2. OpenClaw / Clawbot (продакшн-оркестратор)

Фрагмент: `config/openclaw.mcp-metricool.example.json`  
Полный пример Agent 6: `deploy/openclaw.agent6-publisher.example.json`

### Вариант A — через CLI (рекомендуется)

```bash
openclaw mcp add metricool \
  --command uvx \
  --args "--upgrade" \
  --args "mcp-metricool" \
  --env "METRICOOL_USER_TOKEN=..." \
  --env "METRICOOL_USER_ID=..."

openclaw mcp status --verbose
openclaw mcp probe metricool
```

### Вариант B — вручную в `~/.openclaw/openclaw.json`

Добавьте блок `mcp.servers` из `config/openclaw.mcp-metricool.example.json`.

Проверка:

```bash
openclaw mcp doctor
openclaw gateway
```

---

## 3. Hermes и другие MCP-клиенты

Любой runtime с поддержкой MCP (Hermes, n8n MCP node, Make) использует тот же сервер:

- **command:** `uvx`
- **args:** `["--upgrade", "mcp-metricool"]`
- **env:** `METRICOOL_USER_TOKEN`, `METRICOOL_USER_ID`

`blogId` указывается в параметрах конкретного tool при вызове (как в REST).

---

## Архитектура: все агенты вместе

```
Coordinator (Clawbot/OpenClaw)
    │
    ├── Agent 2 CRM     → skill notion-crm (REST)
    ├── Agent 3 Video   → exec ffmpeg / R2
    ├── Agent 5 Seedance→ MCP Higgsfield (уже есть в agent 4)
    └── Agent 6 Publisher
            ├── exec publish_telegram.py     (TG, lock, Notion)
            ├── exec publish_pipeline.py     (боевой Metricool + Notion)
            ├── MCP metricool                (аналитика, слоты, конкуренты)
            └── MCP chatplace              (воронка IG/TikTok — phase 5)
```

**Правило Agent 6:**
- Подпись Metricool — всегда колонка **«Описание сец.сети»**
- TG — **«Описание для Telegram»**
- Фото — колонка **«Фото»**

---

## ChatPlace (phase 5)

MCP уже в `/.cursor/mcp.json` и `deploy/openclaw.agent6-publisher.example.json`.

| Файл | Назначение |
|------|------------|
| `config/chatplace-mcp.cursor.example.json` | Шаблон для других проектов |
| `config/openclaw.mcp-chatplace.example.json` | Фрагмент для Clawbot |
| `skills/chatplace-funnel/SKILL.md` | Правила для coordinator |

Включение после E2E:
1. `CHATPLACE_API_KEY` в `.env`
2. `chatplace.enabled: true` в `config/publisher.json`
3. Toggle **chatplace** ON в Cursor Customize → MCP

---

## Ссылки

- [Metricool API docs](https://app.metricool.com/resources/apidocs/index.html)
- [mcp-metricool на PyPI](https://pypi.org/project/mcp-metricool/)
- [OpenClaw MCP CLI](https://docs.openclaw.ai/cli/mcp)
- Проверка REST без MCP: `python3 scripts/list_brands.py`
