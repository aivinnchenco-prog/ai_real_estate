# Knowledge Pack V2 — Design (Agent 6 / Agent 7)

**Status:** Design only — not deployed to retrieval index.  
**Rule:** deterministic business logic stays in **code**; this pack shapes **how to speak**, not **what is true**.

---

## 1. Principles

| In knowledge | Not in knowledge |
|--------------|------------------|
| Tone, empathy, brevity | Facebook 6-month minimum |
| Progressive question examples | Matching filter math |
| Implicit confirmation phrasing | Owner check gates |
| Objection responses | HUMAN_HANDOFF silence |
| Repair language | Correlation / idempotency |
| Handoff politeness | Price calculation |
| Anti-patterns | amo stage transitions |

Entries type `HARD_RULE_REFERENCE` may point to code paths but cannot change behavior when edited.

---

## 2. Directory layout (target)

```text
knowledge/v2/
  README.md
  agent6/
    00_style_and_brevity.md
    01_progressive_questions.md      # 1 Q examples, implicit confirm
    02_specific_object_flow.md       # narrative only
    03_open_search_flow.md
    04_implicit_confirmation.md
    05_objections/
      price_too_high.md
      discount_request.md
      airbnb_comparison.md
      think_later.md
      no_budget.md
    06_repair_language.md
    07_contradiction_clarify.md
    08_owner_pending_language.md     # status OK; not blocking new search
    09_new_search_pivot.md             # «новый дом» pivot examples
    10_booking_language.md
    11_handoff_client_language.md
    12_anti_patterns.md                # mandatory section
    MY_NEW_RULES.md
  agent7/
    (owner channel etiquette — split by WA/TG/Airbnb/FB)
  index.json                           # generated v3
```

Legacy `БАЗА_ЗНАНИЙ_AGENT_6_КЛИЕНТЫ.md` remains until v2 index replaces retrieval.

---

## 3. ANTI-PATTERNS section (required content)

Editors must enforce these in `12_anti_patterns.md`:

1. **Анкета 5–7 вопросов** одним сообщением, когда часть данных уже известна.
2. **Повтор уже известного вопроса** (даты, бюджет, район после явного ответа).
3. **Бесконечный wait-template** при смене intent («новый дом» ≠ «ждём владельца»).
4. **Спор с новым intent** клиента — признать pivot, не защищать stale state.
5. **Длинное объяснение** вместо следующего действия (показать варианты / один вопрос).
6. **Выдумывание availability** или ответа владельца.
7. **Повторное приветствие** в активном диалоге.
8. **«Чем ещё помочь?»** когда next_best_action уже определён (показать варианты, ждать владельца, бронь).

Each anti-pattern: Ситуация / Как действовать / Что нельзя / Пример хорошего ответа.

---

## 4. Retrieval v2 (target)

`RetrievalContext` extensions:

```text
missing_hard_slots: []
missing_soft_slots: []
next_best_action: string
objection: string
repair_mode: bool
intent: string
```

Retrieve max 4–6 entries; never inject SECTION bodies into extract schema.

---

## 5. Migration steps

1. Author v2 markdown files (style + anti-patterns first).
2. `python3 scripts/knowledge.py update` with dual-source or v2-only flag.
3. Shadow mode: log `knowledge_selected` without changing qualifier.
4. A/B polish only when eval shows no fact drift.
5. Deprecate duplicate sections in v1 files (pointer stubs).

---

## 6. CLI (unchanged)

```bash
python3 scripts/knowledge.py update
python3 scripts/knowledge.py retrieve --agent AGENT6 --message "..."
```

---

*Design version: 2026-08-19.*
