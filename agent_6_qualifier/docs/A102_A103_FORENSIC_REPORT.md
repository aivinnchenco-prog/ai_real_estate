# Forensic report A102 / A103 (Qualifier + CRM + prices)

Дата прогона: 2026-09-01. Код не менялся.  
«Сегодня» для извлечения дат заморожено на **2026-08-26** (день живого чата).  
Полный дамп прогона: [`A102_A103_replay.json`](./A102_A103_replay.json).

Нарезка входящих — отдельные WhatsApp-пузыри, как в amoCRM PDF (цитаты UI не включены).

---

## 1. Воспроизводящий прогон

**Вывод.** Текущий Qualifier на тех же репликах воспроизводит живые симптомы: Threads-ссылка не резолвится; «сервис дог» становится `pets=True`; районы пишутся кириллицей как есть и обнуляют выдачу; оффер 7 / 8 / 5 000 THB/мес уходит без `skip_polish`; «почему так дешево» не ловится как реакция и либо переспрашивает бюджет, либо (при пустых `districts`) снова ранжирует те же три id.

### 1.1 A103 — linusikdrm

| # | Входящее | Gemini raw (до merge) | districts / pets / budget / guests / dates | Шаблон | skip_polish | Финальный текст клиенту |
|---|----------|------------------------|--------------------------------------------|--------|-------------|-------------------------|
| 1 | «Здравствуйте , увидела объявление что сдается вилла» | `{"language":"ru"}` | `[]` / `None` / `None` / `None` / — | `CLIENT_ASK_OBJECT_OR_SEARCH` | **True** | «Здравствуйте! … конкретный объект … или подобрать …?» |
| 2 | «какая цена ?» | `{}` | без изменений | `build_progressive_reply` (слоты CHECK_IN, GUESTS) | **True** | дата заезда + сколько человек |
| 3 | «на какой период ?» | `{}` | без изменений | то же | **True** | то же (повтор) |
| 4 | «нужен ли депозит» | `{}` | без изменений | то же | **True** | то же (повтор) |
| 5 | заезд середина октября, 3 мес, 4 взрослых, сервис дог | `{"check_in":"2026-10-15","guests":4,"pets":true,"stay_months":3}` | `[]` / **`True`** / `None` / `4` / in `2026-10-15`, stay 3 | `build_progressive_reply` (DISTRICTS, BUDGET) | **True** | «Понял: на 3 месяца, заезд 15.10.2026, 4 гостя. Какой район вам интересует?» |
| 6 | «у вас есть варианты без без депозитов ?» | `{}` | без изменений | **`_show_alternatives`** | **False** | полированный оффер трёх id (см. ниже) |
| 7 | «Ката , карон предпочтительно но подойдет любой» | `{"districts":["Ката","Карон"]}` | **`['Ката','Карон']`** / True / None / 4 | `CLIENT_ASK_BUDGET_TOLERANCE` | **False** | «В вашем бюджете вариантов пока … немного. … расширить…» |
| 8 | «почему так дешево ? номера студии от 15.000 бат в месяц» | `{"bedrooms":0,"budget":15000}` | `['Ката','Карон']` / True / **15000** / 4 | `CLIENT_ASK_BUDGET_TOLERANCE` | **False** | снова «выбор невелик… скорректировать бюджет» |

`language` в `LeadProfile` нет (поле живёт в `session.language`, выставляется из update в `qualifier.py:324-325`). `bedrooms: 0` из хода 8 отброшен: `apply_update` пишет спальни только при truthy (`brain.py:164-165`).

**Факт по pets:** «питомец сервис дог» → Gemini `pets: true` → `LeadProfile.pets=True`. Слот есть и сработал.

**Факт по districts (linusikdrm):** целиком кириллица `['Ката', 'Карон']`, без нормализации в `Kata`/`Karon`. «подойдет любой» в список не попало и фильтр не сняло.

#### Стадии подбора (после каждого хода)

Hard = `capacity_ok ∧ bedrooms_ok ∧ pets_ok`. Далее `district_ok` → `budget_ok` → `availability_ok` → сортировка `price_month` → `_rank` (`find_alternatives_for_session`, limit 10).

Ходы 1–4 (пустой профиль): pool 89 → hard 89 → district 89 → budget 89 → availability **87** → rank топ:  
`A_20260717_006 7 Laguna`, `A_20260717_005 8 Thalang`, `A_20260717_002 5000 Layan`, затем `A_20260716_005 5000`, `A_20260717_004 8000`.

Ход 5 (`pets=True`, 4 гостя): hard **87** (отсеклись объекты с `pets_allowed=False` и/или малой вместимостью). Rank тот же топ-3.

Ход 6 (депозит → `_WANTS_SELECTION_RE` → MVC уже ready): shortlist из трёх дешёвых. `shown_object_ids` = эти три.

Ходы 7–8 (`districts=['Ката','Карон']`): **district_ok = 0** (кириллица не входит в `Kata`/`Karon`). Пустая выдача → `CLIENT_ASK_BUDGET_TOLERANCE` (`qualifier.py:718-728`). Бюджет 15000 на ходе 8 не помогает: район уже обнулил пул.

Черновик оффера хода 6 (`skip_polish=False`):

```
Вот что могу предложить:

Шамбала | Новая уютная вилла с 2 спальнями и бассейном — район Laguna — 7 THB/мес
https://t.me/OpenHome_th/294
https://pub-d08057e4c09d474eb079ccbefc433360.r2.dev/A_20260717_006/photos/index.html
…
Anchan Flora … — район Thalang — 8 THB/мес
…
Отдых на берегу озера… — район Layan — 5 000 THB/мес
```

Полировка **не исказила числа и URL**, но сменила интро («Вот что мы нашли для вас на Пхукете») и латинизировала «Лагуна»/«Таланг».

### 1.2 A102 — Геннадий Ермолаев

| # | Входящее | Gemini raw | districts / pets / budget / guests / dates | Шаблон | skip_polish |
|---|----------|------------|--------------------------------------------|--------|-------------|
| 1 | Hello + `https://www.threads.com/share/_ycvKxUXU/` + условия брони | `{}` | пусто | `CLIENT_ASK_OBJECT_OR_SEARCH` | True |
| 2 | «Можно и варианты» + 28.08–28.09, семья 4, **Район: Равайи**, 1–2 BR | `{"bedrooms":2,"check_in":"2026-08-28","check_out":"2026-09-28","districts":["Равайи"],"guests":4,"stay_months":1,"wants_alternatives":true}` | **`['Равайи']`** / None / None / 4 / 28.08–28.09 | `CLIENT_ASK_BUDGET_TOLERANCE` | False |
| 3 | «Равайи или иные» | `{"districts":["Равайи"],"wants_alternatives":true}` | всё ещё **`['Равайи']`** (не «Равайи или иные», «иные» не добавлены) | то же | False |
| 4 | «Ясно ты в лучшем случае ИИ)» | `{}` | без изменений | то же | False |
| 5 | «Про бюджет я ничего не писал…» | `{"wants_alternatives":true}` | без изменений | то же | False |

**Факт по districts (Геннадий):** значение **`Равайи` целиком, как извлёк Gemini**. Не `Rawai`, не «Равайи или иные». `apply_update` только append уникальных строк (`brain.py:160-163`).

Ход 2+: hard 87 → **district_ok 0** (`«равайи»` ⊄ `rawai`) → пустой `_rank` → фраза про «мало вариантов в бюджете» при `budget is None`. `budget_ok` при пустом бюджете всегда True (`matching.py:16-18`); режет именно район.

Threads: `normalize_publication_url(...)` = `None` → `chosen=None`.

### 1.3 Точки веток (код)

- Вопрос «объект или подбор»: `qualifier.py:562-569` → `CLIENT_ASK_OBJECT_OR_SEARCH` (`templates.py:199`), `skip_polish=True`.
- Анкета: `qualifier.py:602-624` → `build_progressive_reply` (`progressive_templates.py:79`), `skip_polish=True`.
- «есть варианты…»: `_WANTS_SELECTION_RE` `qualifier.py:98-104, 335-336` → при MVC `_show_alternatives` `571-586 / 693-737`, **`skip_polish` по умолчанию False**.
- Пустая выдача: `CLIENT_ASK_BUDGET_TOLERANCE` `templates.py:221`, `qualifier.py:726-728`, **без `skip_polish=True`**.

---

## 2. Цены (Агент 1/2 → Notion)

**Вывод.** Гипотеза «70 000 / 80 000 усечены до 7 / 8» **не подтверждается**. В «Цену за месяц» попал **первый `\d+ бат` из описания Airbnb**: тариф за кВт⋅ч и залог. Чипы `monthly_prices` пустые, потому что Airbnb-сбор месяцев не записал multi_select, а текстовый парсер перезаписал только числовое поле. Это **не системное усечение всех объектов**: `< 1000` ровно эти 2 карточки.

### 2.1 Сырой Notion-JSON свойств

Все три: `Цена за месяц` type=`number`; `monthly_prices` type=`multi_select`, value=`[]`.

| Объект ID | Цена за месяц | monthly_prices | Район | Источник |
|-----------|---------------|----------------|-------|----------|
| `A_20260717_006` | **7** | `[]` | Laguna | airbnb.ru/rooms/**52291355** |
| `A_20260717_005` | **8** | `[]` | Thalang | airbnb.ru/rooms/**1370042851192128227** |
| `A_20260717_002` | **5000** | `[]` | Layan | airbnb.ru/rooms/**1332556287989637851** |

`Залог` у всех трёх = `null`. В описаниях при этом есть депозиты 10 000 / 30 000 / 5 000 ฿.

### 2.2 Что должно быть у источника

В Notion-описании (скопировано с Airbnb) **нет месячной арендной цены**. Есть:

- 006: «Плата за электричество … составляет **7 батов** за кВт⋅ч» + депозит **10 000 батов**
- 005: «**8 бат** за кВт/ч» + депозит **до 30 000 батов**
- 002: «залог … **5000 батов**» (коммуналка включена; арендной цифры нет)

Повторный `parse_listing` по этим текстам даёт ровно `7.0 / 8.0 / 5000.0`, `deposit=None`.

### 2.3 Масштаб (живая база, 89 страниц)

| Метрика | N |
|---------|---|
| Всего страниц | 89 |
| `Цена за месяц` пусто | 4 |
| `price_month < 1000` | **2** (только 006=7 и 005=8) |
| `price_month < 5000` | **2** (те же; 002=5000 не входит) |
| `monthly_prices` пусто | **39** |

Вывод: сломанные 7/8 — точечный баг текстового парсера, не «все цены поделили на 10 000». Пустые чипы — массовее (39/89): отложенный Airbnb-сбор.

### 2.4 Где пишется цена

1. Текст объявления → `listing_parser.parse_listing`  
   `agent_2_registrar/_import/assistant-media/scripts/listing_parser.py:190-196` (`_to_float` `:92-95`). Первый `(\d+) … бат/THB/฿` не за год.
2. Залог (не сработал): `_parse_deposit` `:98-114` — ждёт число сразу после «депозит/залог», не «депозит за сохранность … в размере N».
3. Сид месяцев из карточки Airbnb «Цена»: `pricing_orchestrator.seed_from_listing_price`  
   `agent_1_parser/airbnb_parser/pricing_orchestrator.py:72-115`. Stay &lt; 27 дней без «помесячно» **не** пишется.
4. Чипы + «Цена за месяц» из monthly: `apply_parsed_meta`  
   `agent2_structurize.py:220-237`. Если `prices_deferred` и priced months &lt; `PRICE_MIN_MONTHS` (default 3) — **чипы очищаются**, число из сида всё равно ставится.
5. Перезапись числом из текста: `build_notion_properties` `agent2_structurize.py:306-307` — `draft.price_monthly` **поверх** сида.

**Пара «источник → Notion» для 006:**  
распарсили из «7 батов за кВт⋅ч» → `price_monthly=7` → записали `Цена за месяц=7`; чипы не записаны (`prices_deferred` / нет 3 месяцев); «Залог» пуст, хотя в тексте 10 000 ฿.

---

## 3. Каноник районов

**Уникальные «Район» в Notion (как хранятся, латиница):**

Layan 20, Laguna 16, Thalang 15, Choeng Thale 10, Bang Tao 6, Kamala 5, Chalong 3, **Rawai 3**, Surin 2, **Karon 2**, **Kata 1**, Kathu 1, Mai Khao 1, Nai Harn 1, Patong 1, Phuket 1, Si Sunthon 1. Кириллицы в поле нет.

**Словарь RU↔EN есть, но только в Агенте 2**, Qualifier его не импортирует:

- `agent_2_registrar/_import/assistant-media/config/zones_mapping.json` aliases `:16-74` (`"равай"→Rawai`, `"ката"/"карон"→Karon`, `"лагауна"/"layan"→Bang Tao`, …)
- `zone_resolver.normalize_district` / `resolve_zone`  
  `agent_2_registrar/_import/assistant-media/scripts/zone_resolver.py:24-44`

В Agent 6 `district_ok` — сырой substring (`matching.py:39-44`). Тест даже фикстурит кириллицу в **листинге** (`test_agent7.py:91-95` `districts=["Раваи"]` vs `district="Раваи"`), не Notion-латиницу.

Алиаса «Равайи» (с «и») в `zones_mapping` нет, есть только `"равай"`.

---

## 4. amoCRM: поля и ฿0

### 4.1 Карта field_id (live GET `/leads/custom_fields`)

| Поле сделки | field_id | type |
|-------------|----------|------|
| Бюджет (мес) | **820029** | numeric |
| Район | **820033** | text |
| Гостей | **820035** | numeric |
| Дата заезда | **820025** | date |
| Дата выезда | **820027** | date |
| Животные | **820037** | select |
| Объект ID | **820023** | text |

Имена заданы в `amo.py:28-44` (`LEAD_FIELDS`). id резолвятся `ensure_lead_fields` `:83-110`.

### 4.2 Вызывается ли `update_lead_fields`

Да, в реальном `client_handler` после ответа: `client_handler.py:205-210` (и ещё раз при брони `:232-234`).

Условие: `session.amo_lead_id` **и** `any(v is not None for v in update.values())`.

**Фактический payload по A102/A103 в логах отсутствует** (нет сохранённого PATCH). Живые сделки:

| | A103 `34023597` | A102 `34022851` |
|--|-----------------|-----------------|
| name | `Lead from (linusikdrm)` | `Lead from (Gennady Ermolaev)` |
| pipeline | **Воронка** `11087782` | то же |
| status | Заявка получена `87078254` | то же |
| `price` | **0** | **0** |
| `custom_fields_values` | **нет ключа / []** | **[]** |
| Другие сделки контакта | нет | нет |

Имя `Lead from (…)` — Wazzup, не Qualifier (`create_lead` даёт `Аренда: {name}` , `amo.py:182-183`). Воронка Qualifier — «Аренда — лиды» `11088150` (`amo.py:17`).

`find_open_lead` (`amo.py:124-137`) смотрит **только** `self.pipeline_id`. Тест это фиксирует: `tests/test_amo_dedup.py` `test_find_open_lead_ignores_other_pipeline`. Wazzup-сделка на «Воронке» не reuse. Второй лид на «Аренда — лиды» не создан → `ensure_amo_lead_whatsapp` (`wa_client_runtime.py:124-166`) либо упал (часто `find_open_lead` до `ensure_pipeline()` → нет `self.pipeline_id`), либо amo не был подключён. Тогда `session.amo_lead_id is None` → **PATCH не уходил**.

Даже при успешном bind `update_lead_fields` (`amo.py:212-230`) **не пишет «Животные»**. Пишет: Объект ID, даты, Контракт на год, бюджет, допуск %, район, гостей, WhatsApp. `price` только если `if lead.budget:` (`:234-235`) — пустой бюджет не трогает price.

### 4.3 Почему карточки пустые

Не «профиль был пуст навсегда». На A103 к ходу 5 уже есть даты/гости/pets; на A102 к ходу 2 — даты/гости/район/спальни. **Запись на эти карточки не отработала** (нет bind + нет CF). Pets в payload всё равно не входят.

### 4.4 ฿0

В JSON сделки `"price": 0` — сохранённый ноль Wazzup-создания, не «пустое число отображается как 0 в UI без записи». Qualifier **нигде не ставит `budget=0`** (поиск `budget = 0` в пакете пуст). `create_lead` пишет бюджет только при `lead.budget is not None` (`:165-167`) и **не выставляет** `body["price"]` при создании. `update_lead_fields` ставит `price` только при truthy budget.

---

## 5. WhatsApp: дебаунс

**Точка входа одного пузыря = один ход**

`POST /webhooks/wazzup` (`webhook_http.py:29, 34-59`)  
→ `process_wazzup_webhook` (`webhook.py:87`, цикл `for msg in messages` `:142`)  
→ `process_whatsapp_client_turn` (`wa_client_runtime.py:202`)  
→ `process_client_message` (`client_handler.py:29`)  
→ `extract_lead_update` + `Qualifier.handle_message`.

`social_inbound.py` — контракт FB/PMP, не WA.  
`sessions.py` — persist JSON, не inbound.  
Агрегации пачки **нет**: каждый inbound сразу идёт в `handle_message`.

`WAZZUP_STAGE_MODE` default **true** (`wazzup_config.py:50, 122`): максимум **один live-ответ на один HTTP-батч** (`webhook.py:194-197`). Если Wazzup шлёт **одно сообщение на POST** (типично), дебаунса между пузырями нет.

**Куда встраивать окно склейки:** в `process_wazzup_webhook` **до** `process_whatsapp_client_turn` / в буфер по `chat_id` с таймером. Мешает то, что обработка хода начинается сразу в этом цикле и уже зовёт Gemini + Qualifier + send.

---

## 6. Реакции, «дёшево», депозит

### Таксономия `reactions.py`

`ReactionType` `:19-29`: LIKE, DISLIKE, TOO_EXPENSIVE, BAD_LOCATION, TOO_SMALL, TOO_LARGE, STYLE_DISLIKE, STYLE_LIKE, NO_POOL, OTHER.

Порядок `_PATTERNS` `:72-101` (первый match побеждает):

| Тип | Сигналы |
|-----|---------|
| TOO_EXPENSIVE | «слишком дорог / дорого / не по карману» → price/price_sensitive negative 1.5 |
| BAD_LOCATION | далеко / неудобно расположен |
| NO_POOL | нет/маленький бассейн |
| TOO_SMALL / TOO_LARGE | маловат / великоват |
| STYLE_LIKE / STYLE_DISLIKE | современнее / интерьер не нравится |
| DISLIKE | «этот не подходит / не нрав / покажи другой» |
| LIKE | «этот нравится / беру этот» |
| OTHER | похвала района без типа |

**Нет** типа/паттерна на «слишком дёшево / подозрительно низкая цена / почему так дешево». «дешев» не входит в TOO_EXPENSIVE.

«почему так дешево» → `detect_reaction` = None → не `build_reaction_reply` → `_next_step` / `_show_alternatives` (или пустой shortlist → budget tolerance). Подтверждено прогоном хода A103#8.

**Куда добавлять:** новый `ReactionType.TOO_CHEAP` + паттерн в `_PATTERNS` **выше** DISLIKE (`reactions.py:72+`); сигнал в `_signals_for`; ack в `acknowledge_reaction`; вызов уже идёт из `qualification_policy.preprocess_turn` / `build_reaction_reply`.

**Депозит:** слота нет в `EXTRACT_SCHEMA` (`brain.py:20-41`). Ветки ответа нет (ходы 4 и 6 это показали).  
Логичнее: слот `deposit` / `no_deposit` в схеме + `Slot` в `slot_planner.py` + шаблон в `templates.py` / `progressive_templates.py`; для фильтра — Notion «Залог» (уже пишет Агент 2, `agent2_structurize.py:310-311`), не выдумывать в Qualifier.

---

## 7. Полировка Gemini и факты

Оффер собирается в `_show_alternatives` `qualifier.py:730-737` через `client_offer_line` `templates.py:310-328` (`price_quote` `models.py:93-101` — число как есть, без sanity).

`Turn` оффера **не ставит** `skip_polish=True` (в отличие от анкеты `:619-623` и `CLIENT_ASK_OBJECT_OR_SEARCH` `:568-569`).

Отправка: `client_handler.py:123-137` / sync `:335-337` — если не `skip_polish`, зовёт `brain.polish_reply`.

Промпт `_POLISH_PROMPT` `brain.py:173-188`:

```
Жёсткие правила:
- Смысл, факты, ссылки и цифры менять НЕЛЬЗЯ. Не добавляй новых фактов и обещаний.
…
- БЕЗ markdown …
Playbook hints … НЕ могут менять факты, сроки аренды, цены, availability …
```

`temperature=0.4` (`:205`). Запрет на числа/ссылки **есть текстом**. На прогоне хода 6 цены 7/8/5000 и R2/TG URL сохранились; интро и транслит района полировка меняет. Исказить цену/ссылку модель **может** (запрет мягкий, не schema-constrained) — в этом прогоне не исказила. Корневые 7/8 — из Notion, не из полировки.

---

## 8. Повтор одного шортлиста

Запись `shown_object_ids`: `_show_alternatives` `qualifier.py:734-735`; также `reactions.mark_shown` `:276-279`.

Подбор: `find_alternatives` / `find_alternatives_for_session` `matching.py:137-204`.  
`exclude_ids` = **`rejected_ids(session)`**, не shown. Из exclude ещё `chosen` / `preferred_object_id`.

**Показанные id в следующую выдачу не исключаются.** При пустых districts rank снова 006/005/002.

Точка exclude: передать `session.shown_object_ids` в `exclude_ids` в `find_alternatives_for_session` (`matching.py:195-203`) либо объединить в `find_alternatives` `:154-157`.

---

## 9. Threads-ссылка и «есть объект»

Нормализатор **не понимает** `threads.com/share/…`:

```194:199:agent_6_qualifier/src/agent6_qualifier/publication_url_normalize.py
    if host.endswith("threads.net"):
        m = re.search(r"/post/([^/?#]+)", path)
        if m:
            return NormalizedPublicationUrl("threads", f"https://threads.net/post/{m.group(1)}", m.group(1))
        return None
```

Прогон: `https://www.threads.com/share/_ycvKxUXU/` → `None`.  
Тот же id как `threads.net/post/_ycvKxUXU` нормализатор **принял бы**.

Решение «есть объект»: `Qualifier._resolve_listing` → `resolve_publication_reference` (`publication_resolver.py:52-164`): URL → normalize → mapping store; иначе object_id / utm / parent_post. `None` → `found=False` → `CLIENT_ASK_OBJECT_OR_SEARCH`.

В Notion **нет** `post_url_threads` с `_ycvKxUXU`. Живые threads-URL вида `https://www.threads.com/open.home.th/post/Dcqw3eCjXUt` — тоже не матчятся (host `threads.com`, не `threads.net`).

**Починить только host недостаточно** для `/share/…`: нужен expander редиректа share→`/post/{id}`, плюс строка в publication mapping (`post_url_threads` / mapping store). Лукап по сырому share-id сейчас ничего не найдёт.

---

## 10. Тесты и запрет переименований

### Покрытие

| Зона | Есть | Нет / дыры |
|------|------|------------|
| matching district_ok / budget_ok / rank | `tests/test_agent7.py`: `test_budget_within_10_percent`, `test_district_filter`, `test_pets_empty_field_is_ok`, `test_busy_listing_excluded…`, `test_alternatives_sorted…`; Wave3 rank/exclude в `test_wave3_qualification_policy.py` (`test_rejected_object_excluded…`, preference score) | нет `test_matching.py`; district-тест на кириллице=кириллице, не RU↔EN |
| slot_planner | косвенно: progressive/MVC в `test_qualifier.py`, слоты Wave3 в `test_wave3_qualification_policy.py` (`test_missing_slot…`, confidence) | **нет** `test_slot_planner.py`; MVC без района/бюджета не зафиксирован как контракт |
| qualifier ходы / пустая выдача | `test_qualifier.py` (`test_greeting_without_object_asks_object_or_search`, `test_no_object_goes_straight_to_matching`, `test_alternatives_shown_after_consent`, …) | нет кейса «пустой budget + несовпавший район → ASK_BUDGET_TOLERANCE»; нет «депозит / слишком дёшево» |
| reactions | `test_wave3_qualification_policy.py` `test_detect_reaction_examples` и record/ack | нет TOO_CHEAP / «почему так дешево» |
| publication_url_normalize | параметризация в `test_publication_resolver.py:115` | **нет Threads** (`threads.com` / `/share/`) |
| amo | `test_amo_dedup.py`, `test_amo_tasks.py`, `test_amo_worker_ops.py`; `test_client_handler_service.py` мокает `update_lead_fields` | нет теста payload полей (гости/район/pets/price=0); нет bind Wazzup «Воронка» vs «Аренда — лиды» |

### ROLE_MAP.md — не трогать (`ROLE_MAP.md:54-64`)

1. Notion-колонки `agent6_*` (Publisher).
2. Legacy-пакеты `agent7` / `agent8` (shims).
3. `agent_4_publisher/config/publisher.json` секция `"agent6"`, `schema/notion_schema.json`.
4. Telethon `TG_SESSION=agent7_userbot`.
5. Persisted session JSON, имена стадий amo, Notion paths, alert names `agent7.handler` / `agent8.auto`.

Имена кастомных полей amo в `LEAD_FIELDS` и Notion «Цена за месяц» / `monthly_prices` / «Район» — прод-контракт; переименование сломает CRM.

---

## Сводная таблица симптом → корень → место правки (без применения)

| Симптом | Корень (файл:строки) | Предлагаемое место правки |
|---------|----------------------|---------------------------|
| Оффер 7 / 8 / 5 000 ฿ | `listing_parser.py:190-196` берёт первый «N бат» (кВт⋅ч / залог); `agent2_structurize.py:306-307` пишет в «Цена за месяц»; Qualifier `models.py:93-101` печатает as-is | Ужесточить regex цены (аренда/месяц, min threshold); не писать price&lt;1000 без monthly chips; sanity в `price_quote` |
| Пустые чипы месяцев (39/89) | `agent2_structurize.py:220-227` чистит chips при `prices_deferred`; Airbnb collect не добрал 3 месяца | Добор `pricing_orchestrator` / не считать карточку «с ценой» без chips |
| «Равайи» / «Ката, Карон» → 0 выдачи | Gemini пишет кириллицу as-is `brain.py:160-163`; `district_ok` `matching.py:39-44` без алиасов; словарь есть только в Agent 2 `zones_mapping.json:16-74` | Нормализация в `apply_update` или `district_ok` через тот же alias map; «или иные»/«любой» = снять hard district |
| «Мало вариантов в бюджете» без бюджета | `budget_ok` True при `budget is None` `matching.py:16-18`; пустой shortlist → `CLIENT_ASK_BUDGET_TOLERANCE` `qualifier.py:718-728` | Другой шаблон при `budget is None` (район/критерии); не винить бюджет |
| «сервис дог» не в карточке amo | Extract `pets=True` ок; `update_lead_fields` `amo.py:212-230` **не пишет** Животные; сделки Wazzup не bind | Добавить pets в PATCH; bind к воронке Wazzup или `find_open_lead` без фильтра pipeline |
| Поля Гостей/Район/Даты/Объект ID пустые | `amo_lead_id` не проставлен на `34023597`/`34022851`; `find_open_lead` игнорит «Воронку» `amo.py:134-136` | Reuse Wazzup lead; логировать PATCH |
| ฿0 в карточке | Wazzup `price=0`; Qualifier 0 не пишет | Не писать 0; отображение/инициализация Wazzup |
| Каждый WA-пузырь = новый ход, повтор анкеты | `webhook.py:142` + нет debounce; progressive `qualifier.py:606-624` | Склейка до `process_whatsapp_client_turn` |
| «почему так дешево» → снова оффер/бюджет | Нет TOO_CHEAP `reactions.py:19-101` | Новый ReactionType + паттерн; не `_show_alternatives` |
| Вопрос про депозит игнорируется | Нет слота `brain.py:20-41`; `_WANTS_SELECTION_RE` на «есть варианты» `qualifier.py:98-104` | Слот + шаблон; не трактовать как согласие на подбор |
| Оффер полируется | `_show_alternatives` без `skip_polish` `qualifier.py:737`; polish `client_handler.py:123-137` | `skip_polish=True` на оффере **или** schema-constrained polish |
| Тот же шортлист повторно | `shown_object_ids` пишется `qualifier.py:734-735`, в exclude не идёт `matching.py:195-203` | `exclude_ids |= shown_object_ids` |
| Threads share не резолвится | `publication_url_normalize.py:194-199` только `threads.net` + `/post/` | host `threads.com`, path `/share/` + expander; mapping `post_url_threads` |
| MVC без района/бюджета | `slot_planner.py:93-99` | Включить DISTRICTS (и бюджет?) в MVC open search |
