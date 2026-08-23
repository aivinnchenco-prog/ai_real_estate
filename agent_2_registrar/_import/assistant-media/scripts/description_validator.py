"""
description_validator.py
Страховка после генерации описания слабой моделью (Агент_2).

LLM не умеет надёжно считать символы — даже с явной инструкцией "900
символов" модель иногда промахивается. Эта функция не заменяет промпт
(agent2_description_prompt.txt), а подстраховывает его: проверяет длину
ПОСЛЕ генерации и обрезает по границе строки, если модель промахнулась,
гарантируя, что тег объекта останется в конце в любом случае.

Использование в Agent_2 после вызова слабой модели:
    raw_result = call_weak_model(prompt_filled)
    final_text = validate_and_fit(raw_result, object_id)
    notion.update(page_id, {"Описание для Telegram": final_text})
"""

TELEGRAM_CAPTION_LIMIT = 1024
SAFETY_MARGIN = 60  # запас под непредвиденные переносы/эмодзи

SOCIAL_LIMIT = 700
SOCIAL_SAFETY_MARGIN = 20  # «Описание соц.сети» для IG/TikTok/FB/Threads/LinkedIn/YouTube

X_LIMIT = 280
X_SAFETY_MARGIN = 5  # колонка «Описание X.com» — лимит поста X


def calculate_max_guests(rooms: int | None, extracted_guests: int | None = None) -> int:
    """Вместимость гостей для колонки соц.сети ("до N гостей").

    ПРИОРИТЕТ ИСТОЧНИКА:
    1. extracted_guests — число, извлечённое Gemini из сырого текста
       объявления при структурировании объекта (колонка Notion
       "Вместимость гостей"). Хозяева почти всегда указывают вместимость
       явно ("до 5 гостей", "sleeps 6") — это факт из первых рук, а не
       предположение, и он учитывает диван-кровати, доп. места и т.п.,
       которые формула не видит. ЭТО ОСНОВНОЙ ИСТОЧНИК.
    2. Формула (комнаты × 2 + 1) — используется ТОЛЬКО если в исходном
       тексте вместимость вообще не была указана и extracted_guests
       пуст. Это аварийный фолбэк, не основной путь.

    Если человек не согласен с извлечённым числом — правится напрямую
    в той же колонке Notion "Вместимость гостей", отдельного поля для
    ручной правки не нужно (это и есть то самое поле).
    """
    if extracted_guests is not None:
        return extracted_guests
    if not rooms or rooms < 1:
        rooms = 1  # студия/не указано — считаем как одну комнату
    return rooms * 2 + 1  # фолбэк, только если хозяин нигде не указал число


def _strip_object_tag(generated_text: str, object_id: str) -> str:
    tag = f"Объект №{object_id}"
    if tag in generated_text:
        generated_text = generated_text.replace(tag, "").strip()
    return generated_text


def _fit_by_words(generated_text: str, limit: int) -> str:
    if len(generated_text) <= limit:
        return generated_text
    words = generated_text.split()
    body = ""
    for w in words:
        if len(body) + len(w) + 1 > limit:
            break
        body = f"{body} {w}".strip()
    return body.strip()


def validate_and_fit_social(generated_text: str, object_id: str) -> str:
    """Страховка для «Описание соц.сети» (IG/TikTok/FB/Threads, лимит 700).

    Переносы строк допустимы. Код объекта в тело не дописываем: publisher
    добавляет отдельную строку «🏷 Код объекта: …».
    """
    limit = SOCIAL_LIMIT - SOCIAL_SAFETY_MARGIN
    generated_text = _strip_object_tag(generated_text.strip(), object_id)
    return _fit_by_words(generated_text, limit)


def validate_and_fit_x(generated_text: str, object_id: str) -> str:
    """Страховка для «Описание X.com» (лимит 280, плотный текст без переносов)."""
    limit = X_LIMIT - X_SAFETY_MARGIN
    generated_text = _strip_object_tag(generated_text.strip(), object_id)
    generated_text = " ".join(generated_text.split())
    return _fit_by_words(generated_text, limit)


def validate_and_fit(generated_text: str, object_id: str) -> str:
    """Проверяет длину ответа модели. Если превышен лимит — обрезает по
    последней полной строке (не по середине предложения) и гарантирует,
    что тег объекта останется в конце в любом случае.
    """
    limit = TELEGRAM_CAPTION_LIMIT - SAFETY_MARGIN
    tag = f"#{object_id}"

    generated_text = generated_text.strip()

    if len(generated_text) <= limit:
        # тег на месте? модель иногда забывает, несмотря на инструкцию
        if tag not in generated_text:
            generated_text = generated_text + f"\n\n{tag}"
        return generated_text[:limit] if len(generated_text) > limit else generated_text

    # превышен лимит — обрезаем по строкам с конца, сохраняя тег
    lines = generated_text.split("\n")
    reserved = len(tag) + 4  # + переносы строк
    budget = limit - reserved

    kept = []
    total = 0
    for line in lines:
        if tag in line:
            continue  # тег добавим отдельно в конце, не дублируем
        if total + len(line) + 1 > budget:
            break
        kept.append(line)
        total += len(line) + 1

    body = "\n".join(kept).rstrip()

    # край: модель вернула текст одной длинной строкой без переносов —
    # построчная обрезка даёт пусто. Фолбэк — обрезка по словам.
    if not body:
        words = generated_text.replace(tag, "").split()
        body = ""
        for w in words:
            if len(body) + len(w) + 1 > budget:
                break
            body = f"{body} {w}".strip()

    return body.rstrip() + f"\n\n{tag}"


if __name__ == "__main__":
    # проверка приоритета: извлечённое из текста число всегда побеждает формулу
    print("--- calculate_max_guests ---")
    print("извлечено 6, комнат 2 ->", calculate_max_guests(2, extracted_guests=6), "(источник: текст объявления)")
    print("не извлечено, 2 спальни ->", calculate_max_guests(2), "(фолбэк-формула, эталон: 5)")
    print("не извлечено, студия (None) ->", calculate_max_guests(None), "(фолбэк-формула)")
    print()

    # проверка на слишком длинном примере
    fake_long = "Строка описания с деталями объекта. " * 40
    result = validate_and_fit(fake_long, "A_20260712_003")
    print(f"длина результата: {len(result)} (лимит {TELEGRAM_CAPTION_LIMIT - SAFETY_MARGIN})")
    print("...", result[-80:])

    # проверка на нормальном примере без тега от модели
    normal = "🏡 Вилла · 4 спальни · Чалонг\n\n💰 95 000 ฿/мес"
    print("\n---")
    print(validate_and_fit(normal, "A_20260712_003"))

    # проверка короткой соц-версии на реальном примере из проекта
    print("\n--- social (эталон, тег уже есть) ---")
    social_example = (
        "Квартира в Аренду, Пхукет! LEGENDARY 2BR с видом на бассейн 🌴 "
        "Бангтао, Пхукет. 2 спальни, до 5 гостей. Охраняемый комплекс, "
        "с зонами отдыха. Рядом пляж, кафе, магазины. Больше информации "
        "в ТГ. Цена меняется с сезонностью. Бронь в WhatsApp +66625124002"
    )
    result_social = validate_and_fit_social(social_example, "F_20260702_001")
    print(result_social)
    print(f"длина: {len(result_social)} (лимит {SOCIAL_LIMIT})")

    print("\n--- x.com (эталон 280) ---")
    result_x = validate_and_fit_x(social_example, "F_20260702_001")
    print(result_x)
    print(f"длина: {len(result_x)} (лимит {X_LIMIT})")

    print("\n--- social (слишком длинный, модель не уложилась) ---")
    too_long_social = social_example + (" Дополнительная фраза про комплекс и район." * 40)
    result_trim = validate_and_fit_social(too_long_social, "F_20260702_001")
    print(result_trim)
    print(f"длина: {len(result_trim)} (лимит {SOCIAL_LIMIT})")

    print("\n--- x.com (слишком длинный) ---")
    result_x_trim = validate_and_fit_x(too_long_social, "F_20260702_001")
    print(result_x_trim)
    print(f"длина: {len(result_x_trim)} (лимит {X_LIMIT})")
