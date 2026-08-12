# Как добавить новое правило

Если правило касается **клиента**, откройте:

`БАЗА_ЗНАНИЙ_AGENT_6_КЛИЕНТЫ.md`

Если правило касается **владельца / агента объекта**, откройте:

`БАЗА_ЗНАНИЙ_AGENT_7_ВЛАДЕЛЬЦЫ.md`

Добавьте в раздел **МОИ НОВЫЕ ПРАВИЛА** такой блок:

```markdown
## Название

Ситуация:
...

Как должен действовать агент:
...

Что нельзя делать:
...

Пример хорошего ответа:
...
```

Минимум достаточно так:

```markdown
## Название

Ситуация:
...

Как должен действовать агент:
...
```

Затем выполните:

```bash
python3 scripts/knowledge.py update
```

Готово.

---

## Что важно помнить

- Эти два файла — единственные human-editable источники знаний.
- Старый `БАЗА_ЗНАНИЙ.md` оставлен только как указатель.
- Запись в Markdown **не меняет** жёсткие правила кода:
  Facebook минимум 6 месяцев, ownership/handoff, correlation, роли контактов, idempotency.
- Не пишите сюда API keys / secrets.

---

## Для разработчика

### Архитектура

| Layer | Решает |
|------|--------|
| Code (`rental_policy`, qualifier, ownership, correlation) | WHAT IS TRUE |
| Session state | WHAT WE KNOW |
| Knowledge files + retriever | HOW TO ACT |
| LLM polish | HOW TO SAY IT |

### Файлы runtime

- `src/agent6_qualifier/knowledge/parser.py` — dual-file parse + auto metadata
- `src/agent6_qualifier/knowledge/index.py` — rebuild + last-good fallback
- `src/agent6_qualifier/knowledge/retriever.py` — agent-scoped retrieval
- `src/agent6_qualifier/knowledge/authority.py` — CORE WINS
- `src/agent6_qualifier/knowledge/learning.py` — candidates / approve destination
- `knowledge/knowledge_index.json` — generated cache
- `knowledge/knowledge_index.last_good.json` — last valid cache

### CLI

```bash
python3 scripts/knowledge.py update      # validate + rebuild both files
python3 scripts/knowledge.py validate
python3 scripts/knowledge.py rebuild-index
python3 scripts/knowledge.py list
python3 scripts/knowledge.py show <id>
python3 scripts/knowledge.py approve <id>
python3 scripts/knowledge.py reject <id>
python3 scripts/knowledge.py retrieve --agent AGENT6 --message "..."
```

### Auto metadata

Пользователь не пишет `agents/tags/priority/status`.
Parser назначает:

- Agent6 file → `AGENT6`
- Agent7 file → `AGENT7`
- id → `agent6/...` или `agent7/...`
- status → `CURRENT` / `APPROVED` для ручных правил
- tags → keyword mapping

### Observability

В логах:

```text
knowledge_selected: ['agent6/facebook-только-долгосрочная-аренда', ...]
```

### Fail-safe

Если файл битый / index повреждён — используется last-good index.
Agent6/7 продолжают работать. В лог: `KNOWLEDGE_FALLBACK`.

### Live auto-learning

DISABLED. Только candidate → human review → approve.
