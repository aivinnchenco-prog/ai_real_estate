# Деплой на OpenClaw

## Что упаковать

Заархивируй всю папку проекта (без `.env` и `data/media/*`):

```bash
cd ..
zip -r real-estate-agent.zip "agent 4" \
  -x "*.env" -x "*/data/media/*" -x "*/.DS_Store"
```

Или отправь папку как git-репозиторий.

## На сервере с OpenClaw

```bash
# 1. Распаковать
unzip real-estate-agent.zip
cd "agent 4"

# 2. Зависимости (если ещё нет)
# ffmpeg — для video-агента
# curl — для API-вызовов

# 3. Setup
./scripts/validate.sh
./scripts/setup.sh

# 4. Секреты
nano .env   # Metricool, Noushen CRM, Facebook

# 5. Конфиг OpenClaw
cp ~/.openclaw/openclaw.json.generated ~/.openclaw/openclaw.json
# Проверь model.primary под свой провайдер

# 6. Запуск
openclaw gateway
```

## Проверка после деплоя

1. Напиши coordinator: `/status test` — должен ответить
2. `/parse <url>` — проверка parser без полного пайплайна
3. Посмотри `data/listings/` — появился raw.json?

## Каналы (Telegram / WhatsApp)

Раскомментируй `bindings` в `openclaw.json`:

```json5
bindings: [
  { agentId: "coordinator", match: { channel: "telegram" } },
],
```

Настрой канал через `openclaw configure`.

## Что донастроить под себя

| Компонент | Файл |
|-----------|------|
| Селекторы сайтов | `workspaces/parser/skills/parse-listing/reference.md` |
| Noushen CRM API | `workspaces/crm/skills/noushen-crm/SKILL.md` |
| Metricool endpoints | `workspaces/publisher/skills/publish-metricool/SKILL.md` |
| FB Marketplace API | `workspaces/publisher/skills/publish-fb-marketplace/SKILL.md` |
| FB Groups | `workspaces/publisher/skills/publish-fb-groups/SKILL.md` |

## Troubleshooting

- **Sub-agent не spawn'ится** — проверь `tools.profile: "coding"` и `allowAgents`
- **Video timeout** — увеличь `runTimeoutSeconds` для video-агента до 1200
- **403 при парсинге** — задай `PARSER_PROXY_URL` в `.env`
