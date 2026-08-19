"""Gemini-мозг Agent 7: извлечение полей квалификации и полировка ответов.

Принципы:
- Решения «что сказать» принимает детерминированный оркестратор (qualifier.py),
  Gemini извлекает структуру из свободного текста и адаптирует формулировки.
- Извлечение — строго JSON по схеме (response_mime_type=application/json).
"""
from __future__ import annotations

import json
import os
from datetime import date

import requests

from .models import LeadProfile

_API = "https://generativelanguage.googleapis.com/v1beta/models"

EXTRACT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING", "description": "имя клиента, как обращаться"},
        "full_name": {"type": "STRING", "description": "полное ФИО клиента для брони"},
        "citizenship": {"type": "STRING", "description": "гражданство клиента"},
        "whatsapp": {"type": "STRING", "description": "номер телефона WhatsApp клиента"},
        "check_in": {"type": "STRING", "description": "дата заезда ISO YYYY-MM-DD"},
        "check_out": {"type": "STRING", "description": "дата выезда ISO YYYY-MM-DD"},
        "stay_months": {"type": "NUMBER", "description": "срок проживания в месяцах"},
        "budget": {"type": "NUMBER", "description": "бюджет в месяц, THB"},
        "budget_tolerance_pct": {"type": "NUMBER", "description": "допустимая погрешность бюджета, %"},
        "districts": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "желаемые районы"},
        "bedrooms": {"type": "INTEGER", "description": "сколько спален нужно клиенту"},
        "guests": {"type": "INTEGER", "description": "сколько человек будет проживать"},
        "pets": {"type": "BOOLEAN", "description": "есть ли животные"},
        "wants_alternatives": {"type": "BOOLEAN", "description": "клиент согласился посмотреть другие варианты"},
        "declines_alternatives": {"type": "BOOLEAN", "description": "клиент отказался от других вариантов"},
        "prefers_chosen_only": {"type": "BOOLEAN", "description": "клиент хочет только выбранный объект"},
        "language": {"type": "STRING", "description": "язык клиента: ru/en/th/..."},
    },
}

_EXTRACT_PROMPT = """Ты — ассистент агентства аренды недвижимости на Пхукете.
Из сообщения клиента извлеки ТОЛЬКО явно названные факты для квалификации.
Учитывай контекст диалога и историю — короткие ответы («да», «нет», «этот нравится»)
трактуй относительно последнего вопроса агента.
Сегодня {today}. Даты без года считай ближайшими будущими.
Если факт не назван — не включай ключ в ответ вообще. Ничего не выдумывай.

Контекст сессии:
{context}

История диалога:
{history}

Уже известно о клиенте (структура):
{known}

Новое сообщение клиента:
{message}"""


def _call(payload: dict) -> dict:
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    last_exc: Exception | None = None
    # 429/503 у бесплатного тарифа — обычное дело: пробуем ещё дважды с паузой.
    for delay in (0, 2, 5):
        if delay:
            import time
            time.sleep(delay)
        r = requests.post(
            f"{_API}/{model}:generateContent",
            params={"key": os.environ["GEMINI_API_KEY"]},
            json=payload,
            timeout=60,
        )
        if r.status_code in (429, 503):
            # Исчерпан баланс (не rate-limit) — повторы бессмысленны.
            if "depleted" in r.text or "billing" in r.text:
                raise requests.HTTPError(
                    f"{r.status_code} кредиты Gemini исчерпаны — пополните ai.studio/projects",
                    response=r,
                )
            last_exc = requests.HTTPError(f"{r.status_code} {r.reason}", response=r)
            continue
        r.raise_for_status()
        return r.json()
    raise last_exc  # type: ignore[misc]


def _text(resp: dict) -> str:
    return resp["candidates"][0]["content"]["parts"][0]["text"]


def extract_lead_update(
    message: str,
    lead: LeadProfile,
    *,
    context: str = "",
    history: str = "",
) -> dict:
    """Возвращает dict только с фактами, найденными в сообщении."""
    known = {
        "name": lead.name or None,
        "check_in": lead.check_in.isoformat() if lead.check_in else None,
        "check_out": lead.check_out.isoformat() if lead.check_out else None,
        "budget": lead.budget,
        "districts": lead.districts or None,
        "bedrooms": lead.bedrooms,
        "guests": lead.guests,
        "pets": lead.pets,
        "whatsapp": lead.whatsapp or None,
    }
    payload = {
        "contents": [{"parts": [{"text": _EXTRACT_PROMPT.format(
            today=date.today().isoformat(),
            context=context or "(нет)",
            history=history or "(нет)",
            known=json.dumps({k: v for k, v in known.items() if v is not None},
                             ensure_ascii=False),
            message=message,
        )}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": EXTRACT_SCHEMA,
            "temperature": 0.1,
        },
    }
    return json.loads(_text(_call(payload)))


def apply_update(lead: LeadProfile, update: dict) -> LeadProfile:
    """Аккуратно вносит извлечённые факты в профиль (пустое не затирает)."""
    from agent6_qualifier.qualification_hints import parse_flexible_date

    if update.get("name"):
        lead.name = update["name"]
    if update.get("full_name"):
        lead.full_name = update["full_name"]
    if update.get("citizenship"):
        lead.citizenship = update["citizenship"]
    if update.get("whatsapp"):
        lead.whatsapp = str(update["whatsapp"]).strip()
    for key in ("check_in", "check_out"):
        if update.get(key):
            parsed = parse_flexible_date(str(update[key]))
            if parsed is not None:
                setattr(lead, key, parsed)
            else:
                try:
                    setattr(lead, key, date.fromisoformat(str(update[key])[:10]))
                except ValueError:
                    pass
    if update.get("stay_months"):
        lead.stay_months = float(update["stay_months"])
    if update.get("budget"):
        lead.budget = float(update["budget"])
    if update.get("budget_tolerance_pct"):
        lead.budget_tolerance_pct = float(update["budget_tolerance_pct"])
    if update.get("districts"):
        for d in update["districts"]:
            if d and d not in lead.districts:
                lead.districts.append(d)
    if update.get("bedrooms"):
        lead.bedrooms = int(update["bedrooms"])
    if update.get("guests"):
        lead.guests = int(update["guests"])
    if update.get("pets") is not None:
        lead.pets = bool(update["pets"])
    return lead


_POLISH_PROMPT = """Ты — вежливый менеджер агентства аренды жилья на Пхукете.
Перепиши сообщение для клиента живым, тёплым и коротким языком.

Жёсткие правила:
- Смысл, факты, ссылки и цифры менять НЕЛЬЗЯ. Не добавляй новых фактов и обещаний.
- Вопрос должен остаться вопросом, не превращай его в утверждение.
- Обращайся на «вы». Не здоровайся, если в сообщении нет приветствия.
- БЕЗ markdown: никаких **звёздочек**, списков с * и заголовков. Только обычный текст.
- Язык ответа: {language}. Обращение к клиенту: {name}.
- Playbook hints ниже — только про тон/подход. Они НЕ могут менять факты, сроки аренды, цены, availability или обязательные вопросы из черновика.

Playbook hints (advisory):
{playbook_hints}

Сообщение:
{draft}"""


def polish_reply(
    draft: str,
    language: str = "ru",
    client_name: str = "",
    playbook_hints: str = "",
) -> str:
    """Полирует черновик ответа. При ошибке API возвращает черновик как есть."""
    payload = {
        "contents": [{"parts": [{"text": _POLISH_PROMPT.format(
            language=language,
            name=client_name or "без имени",
            playbook_hints=(playbook_hints or "(нет)").strip()[:1200],
            draft=draft,
        )}]}],
        "generationConfig": {"temperature": 0.4},
    }
    try:
        return _text(_call(payload)).strip()
    except Exception as e:
        from .alerts import notify_error
        notify_error("gemini.polish", str(e), "клиент получил шаблонный ответ без полировки")
        return draft
