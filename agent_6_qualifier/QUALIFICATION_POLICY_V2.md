# Qualification Policy V2 — Agent 6

**Status:** Specification (not runtime).  
**Canonical package:** `agent6_qualifier`  
**Audience:** engineering, QA, knowledge editors, managers reviewing handoff quality.

This document is the **official policy spec**. It is not a prompt dump. Runtime prompts and templates implement subsets of this policy; when code and this doc disagree, **deterministic code wins** until an explicit implementation wave merges the gap.

---

## A. PURPOSE

Agent 6 does **not** optimize for “fill the CRM form.”

**Primary goal:** move the client to the next **useful action** with minimal friction:

| Outcome | Meaning |
|---------|---------|
| **Shortlist** | show a small set of viable options |
| **Owner check** | verify availability / terms with Agent 7 |
| **Booking** | collect booking fields and confirm intent |
| **Human handoff** | structured escalation when automation fails or client insists |

Qualification is **progressive**: extract maximum from each message; ask only what is still required for the **next** action.

---

## B. SOURCE OF AUTHORITY

Priority order (highest first):

1. **Deterministic code / hard business rules** — rental policy, matching filters, ownership gates, correlation, amo stage rules, price/availability gates.
2. **Current SearchContext / session runtime state** — active request, pending owner requests, handoff flags.
3. **Explicit latest client message** — intent, corrections, new search, status questions.
4. **Knowledge / playbooks** — style, objections, examples (advisory).
5. **LLM inference** — extract and polish only; never sole authority for gates.

**Rule:** explicit new client intent must **not** lose to stale workflow state (e.g. `awaiting_owner` must not block `NEW_PROPERTY_SEARCH`).

Knowledge cannot override: Facebook 6-month minimum, HUMAN_HANDOFF silence, owner correlation, role assignment, idempotency.

---

## C. PROGRESSIVE QUALIFICATION

### Removed concept

“There is a mandatory checklist of 5–7 questions every client must answer.”

### Rule

- Parse **every** inbound message for all explicit facts.
- Ask only the **next** parameter required for the next action.
- **Preferred:** 1 question per outbound message.
- **Maximum:** 2 related questions if natural (e.g. dates + guests).
- **Never** ask for a value the client already provided in session or the same turn.

### Anti-pattern (forbidden)

Sending `CLIENT_QUALIFY_BULLETS` (budget + district + bedrooms + check-in + check-out) when the client already supplied several of these in the same or prior message.

---

## D. MINIMUM VIABLE QUALIFICATION (MVC)

Two distinct flows:

### D.1 SPECIFIC OBJECT REQUEST

Client arrived with (or selected) a concrete listing.

| Stage | Required | Optional / soft | Can ask later |
|-------|----------|-----------------|---------------|
| **Before price quote** | `check_in` (or inferable stay window) | `guests`, `stay_months` | budget, district |
| **Before owner check** | `check_in`, rental policy pass, object resolved | `guests`, `stay_months` | budget (never sent to owner) |
| **Before matching alts** | `check_in` helps; object busy triggers alts | budget, districts, bedrooms | pets |
| **Before booking** | `guests`, `full_name`, `citizenship`, `whatsapp` | — | — |

Do **not** require budget/district/bedrooms for owner check on a chosen object if the next action is owner verification.

### D.2 OPEN PROPERTY SEARCH

Client wants options without a fixed object.

| Stage | Required | Optional / soft | Can ask later |
|-------|----------|-----------------|---------------|
| **Before first shortlist** | `check_in` **or** clear stay window; `guests` strongly preferred | budget, districts, bedrooms | pets, housing style |
| **Before owner check on picked alt** | object chosen + `check_in` | guests | budget |
| **Refine shortlist** | at least one narrowing signal | more soft prefs | — |

Do **not** collect CRM fields that do not change the next action (e.g. full budget grid before showing any options when client asked “show me options”).

---

## E. SLOT MODEL (contract — migration optional in Wave 1)

`QualifiedSlot` — logical contract; full `LeadProfile` migration is not required to adopt policy semantics.

```text
QualifiedSlot:
  value: any
  confidence: CONFIRMED | INFERRED | UNKNOWN
  source: explicit_client | inferred | publication | crm | previous_context
  updated_at: ISO-8601 UTC
  correction_history: [{at, old_value, new_value, message_snippet}]
```

**Confidence rules:**

| Level | When |
|-------|------|
| CONFIRMED | explicit client text, or explicit correction, or confirmed implicit ack |
| INFERRED | publication mapping, LLM/hints, contextual inference |
| UNKNOWN | not set |

Qualifier should treat CONFIRMED/INFERRED as “known” for re-ask prevention; UNKNOWN may be asked.

---

## F. HARD VS SOFT CONSTRAINTS

### Hard constraints

Cannot violate without explicit client agreement:

- **Budget cap** when phrasing is limiting (“не выше”, “максимум”, “до X”).
- **Minimum bedrooms** when phrasing is limiting (“минимум”, “не меньше”).
- **Pets required** when client has pets.
- **Dates / stay policy** — Facebook ≥ 6 months when applicable.
- **District-only** when phrasing is exclusive (“только Банг Тао”, “не другой район”).

### Soft preferences

Used for ranking and widening, not hard filter unless client insists:

- preferred district (without “only”)
- near sea, modern interior, pool, view
- budget as “ориентир” without cap language

**Budget is not automatically hard or soft** — classify from client semantics each turn.

---

## G. CORRECTIONS

Examples:

| Client | Action |
|--------|--------|
| «Не 150, а 200 тысяч» | update `budget` only → CONFIRMED |
| «Не Банг Тао, теперь Раваи» | update `districts` only → CONFIRMED |
| «Нас четыре, не два» | update `guests` only |

Do **not** reset entire search or clear unrelated slots.

Archive active owner request if correction implies new search (`NEW_PROPERTY_SEARCH` / `CHANGE_CRITERIA`).

---

## H. OBJECT REACTION MEMORY

Runtime fields (spec; implementation Wave 2+):

```text
shown_object_ids: []
liked_object_ids: []
rejected_object_ids: []
positive_traits: []    # e.g. district:Ravai, modern_interior
negative_traits: []    # e.g. distance, price_high
price_feedback: {}     # object_id -> too_high | ok | unknown
```

Signals:

| Client phrase | Signal |
|---------------|--------|
| «слишком далеко» | `negative_traits: distance/location` |
| «хочу современнее» | `positive_traits: modern_interior` |
| «этот район нравится» | `positive_traits: district:+` |
| «слишком дорого» | `price_feedback: too_high` + optional budget soft adjust |

Next shortlist must incorporate reactions (ranking / exclude rejected).

---

## I. ACTIVE REQUEST SUMMARY

Canonical **runtime truth** (generated deterministically, not free LLM summary):

```text
ACTIVE REQUEST
  intent: SPECIFIC_OBJECT | OPEN_SEARCH | BOOKING | STATUS_QUERY | ...
  source_flow: specific_object | open_search | publication_inbound
  dates: check_in, check_out, stay_months
  guests, budget, areas, bedrooms, pets
  hard_constraints: []
  soft_preferences: []
  shown: [object_ids]
  rejected: [{object_id, reason_tag}]
  chosen: object_id | null
  pending_owner_requests: [{object_id, awaiting_owner, owner_verdict}]
  availability_price_context: {quoted_price, owner_verdict, calendar_precheck}
  next_best_action: enum (see §M)
```

Inject into `build_knowledge()` / handoff notes / manager alerts.

---

## J. CONTRADICTIONS

Deterministic types:

| Type | Resolution |
|------|------------|
| budget_changed | if explicit new amount → replace; if ambiguous → one clarification |
| dates_conflict | if new dates explicit → replace; else clarify |
| stay_changed | update `stay_months`; FB policy re-check |
| guests_changed | update guests |
| object_changed | new chosen / new search intent |
| only_this_object vs new_search | **new_search wins** if explicit |
| year_stay vs explicit checkout | explicit checkout wins; else year contract inference |

**Not every change is an error.** Clarify only when two values are both plausible and active.

---

## K. CONVERSATION REPAIR

### Triggers (configurable thresholds)

- Agent failed to interpret intent **twice** in a row
- Client repeats request with different words
- Frustration signals («ты понимаешь?», «уже третий раз»)
- Repeated near-identical bot reply (same `template_key`)
- Repeated low-confidence / empty extract on critical slots

### Repair response pattern

1. Short recap of what was understood
2. Do **not** repeat the failed script
3. **One** clarifying question max

Example:

> Понял. Старый объект больше не рассматриваем — ищем новый вариант. Какой район вам подходит?

If repair fails after `REPAIR_MAX_ATTEMPTS` (default 2) → `HANDOFF_HUMAN`.

**Partial implementation note:** wait-template loop prevention (`last_outbound_template_key` + forced reclassify) is a precursor; full repair policy is Wave 3.

---

## L. LEAD TEMPERATURE (internal only)

Enum: `COLD | WARM | HOT | STALLED`

Derived from **observable signals**, not LLM opinion:

| Signal | Weight |
|--------|--------|
| concrete check_in | +warm |
| budget stated | +warm |
| object chosen | +warm |
| asked owner / awaiting owner | +hot |
| booking_intent | +hot |
| returned after silence | +warm |
| no reply > SLA | stalled |
| only vague “подумаю” | cold/stalled |

Does **not** override hard business rules. Used for manager metadata and `next_best_action` hints.

---

## M. NEXT BEST ACTION

Canonical enum (computed from runtime state, not LLM):

```text
ASK_CRITICAL_SLOT
SHOW_MATCHES
REFINE_MATCHES
CHECK_OWNER
WAIT_OWNER
OFFER_ALTERNATIVES
START_BOOKING
REPAIR_DIALOGUE
HANDOFF_HUMAN
FOLLOW_UP
NONE
```

Example mapping:

| State | Action |
|-------|--------|
| specific object, no check_in | ASK_CRITICAL_SLOT (dates) |
| open search, enough to match | SHOW_MATCHES |
| shown alts, client reacted | REFINE_MATCHES |
| qualified + object, no verdict | CHECK_OWNER |
| awaiting_owner + CONTINUE intent | WAIT_OWNER |
| busy object | OFFER_ALTERNATIVES |
| owner free, no booking data | START_BOOKING |
| misunderstanding_count high | REPAIR_DIALOGUE |
| human_handoff / HUMAN_REQUIRED | HANDOFF_HUMAN |

---

## N. STRUCTURED HUMAN HANDOFF (amoCRM)

Note format:

```text
[HANDOFF]

Client intent: {intent}
Active request: {ACTIVE REQUEST one-liner}
Hard constraints: {list}
Soft preferences: {list}
Shown objects: {ids}
Rejected objects: {id: reason}
Chosen object: {id}
Pending owner checks: {object_id: status}
Lead temperature: {COLD|WARM|HOT|STALLED}
Reason for handoff: {enum + detail}
Next best action: {enum}
Last relevant client message: "{snippet}"
```

Manager must understand the case without reading full chat history.

---

## O. INTENT MODEL

Canonical intents (deterministic first, optional LLM fallback):

| Intent | Meaning |
|--------|---------|
| `CONTINUE_CURRENT_REQUEST` | status of current object / owner reply |
| `NEW_PROPERTY_SEARCH` | new home / new area / start over |
| `CHANGE_CRITERIA` | adjust budget/district/dates without full reset |
| `GENERAL_QUESTION` | FAQ-style, no search pivot |
| `HUMAN_REQUIRED` | ask for human manager |

On `NEW_PROPERTY_SEARCH` / `CHANGE_CRITERIA`:

- `archive_active_owner_request()` → `pending_owner_requests[]`
- reset active search fields; preserve client identity (name, whatsapp, citizenship, pets, source_channel)
- do **not** block new search on old `awaiting_owner`

---

## P. OWNERSHIP & SILENCE

| Mechanism | Rule |
|-----------|------|
| `human_handoff_active` / `handoff_to_human` | bot silent until TTL elapsed |
| `AGENT6_HUMAN_HANDOFF_TTL_HOURS` | default 48; extend on human activity |
| WA ownership store | separate TTL; see `messaging/ownership.py` |
| `Turn.silent` | no outbound when bot may not respond |

---

## Q. AGENT 7 BOUNDARY

Agent 6 decides **if** owner check is needed. Agent 7 executes outreach.

Payload (no client budget):

- object_id, dates, stay_months, guests, bedrooms, pets, verify_list
- correlation: `owner_request_id`

Owner replies for **archived** requests update `pending_owner_requests` only, not active search branch.

---

## R. KNOWLEDGE PACK V2 (scope)

Knowledge holds **conversational** guidance only. See `knowledge/KNOWLEDGE_PACK_V2_DESIGN.md`.

Deterministic rules stay in code; knowledge may **reference** them as `HARD_RULE_REFERENCE`.

---

## S. ANTI-PATTERNS (knowledge + QA)

- 5–7 question “анкета” in one message when facts already known
- Re-asking known slots
- Infinite wait-template loop on new intent
- Arguing with explicit new client intent
- Long explanation instead of next action
- Inventing availability or owner response
- Re-greeting mid-active dialogue
- «Чем ещё помочь?» when `next_best_action` is already known

---

## T. IMPLEMENTATION WAVES (recommended)

| Wave | Scope |
|------|--------|
| **1** | **IMPLEMENTED** — post-audit stack in canonical refactor |
| **2** | **IMPLEMENTED** — MVC slot planner, progressive questions, ACTIVE REQUEST snapshot, next_best_action |
| **3** | QualifiedSlot confidence; corrections; contradictions; repair counter |
| **4** | Object reaction memory; ranking integration; lead temperature + next_best_action module |
| **5** | Knowledge pack v2 migration; eval suite全部 green on `tier: v2_target` |

---

## U. EVAL SUITE

Production-grade conversation evals live in `tests/eval/`. Scenarios tagged:

- `baseline` — current refactor qualifier (71 scenarios, must pass)
- `post_merge` — Wave 1 post-audit stack (26 scenarios, must pass)
- `wave2` — Wave 2 progressive qualification (7 scenarios, must pass)
- `v2_target` — Wave 3+ features (expected xfail for remaining scenarios)

Run: `pytest tests/eval/test_qualification_eval.py -v`

---

*Document version: 2026-08-19. No runtime behavior enabled by this file alone.*
