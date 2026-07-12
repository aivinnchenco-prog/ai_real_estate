"""Генерация описаний объекта для Notion (Агент 2).

Три колонки:
  «Описание для Telegram» / «Описание для FB Marketplace» — длинный пост
    (промпт config/prompts/agent2_description_prompt.txt, лимит ~964);
  «Описание соц.сети» — короткий тизер без цены
    (промпт agent2_social_description_prompt.txt, лимит 280).

Порядок работы: заполняем промпт фактами → LLM (Gemini, затем Claude — что
настроено в env) → обязательная код-проверка длины (description_validator),
потому что модель не умеет надёжно считать символы. Если LLM-ключей нет или
вызов упал — детерминированный шаблонный фолбэк, тоже через валидатор.

Контакты (WhatsApp, Telegram) — из config/pipeline.json → contacts,
не зашиты в код.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from description_validator import (
    calculate_max_guests,
    validate_and_fit,
    validate_and_fit_social,
)

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

_GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"
_ANTHROPIC_API = "https://api.anthropic.com/v1/messages"


# ---------- промпты ----------

def load_prompt(name: str) -> str:
    return (_CONFIG_DIR / "prompts" / name).read_text(encoding="utf-8")


def fill_template(template: str, mapping: dict) -> str:
    """Подстановка {{key}} → значение (пустая строка, если None)."""
    def repl(m: re.Match) -> str:
        value = mapping.get(m.group(1).strip())
        return "" if value is None else str(value)

    return re.sub(r"\{\{([^}]+)\}\}", repl, template)


# ---------- LLM ----------

def _call_gemini(prompt: str, timeout: int = 60) -> str | None:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 2048,
            # У gemini-2.5 «размышления» тратят выходные токены и обрезают
            # текст на середине — для копирайтинга они не нужны.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    req = urllib.request.Request(
        f"{_GEMINI_API}/{model}:generateContent?key={key}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip() or None
    except (urllib.error.URLError, KeyError, IndexError, ValueError, TimeoutError):
        return None


def _call_anthropic(prompt: str, timeout: int = 60) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
    body = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        _ANTHROPIC_API,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        return "".join(
            b.get("text", "") for b in data.get("content", [])
        ).strip() or None
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return None


def call_llm(prompt: str) -> str | None:
    """Gemini → Claude → None (фолбэк на шаблон решает вызывающий)."""
    return _call_gemini(prompt) or _call_anthropic(prompt)


# ---------- контекст объекта ----------

def resolve_max_guests(draft, parsed_meta: dict | None = None) -> tuple[int, bool]:
    """(вместимость, указана_ли_хозяином).

    Приоритет: явное число из текста объявления → personCapacity из
    Airbnb parsed.json (хозяин задаёт его сам в листинге) → формула
    комнаты×2+1 (аварийный фолбэк).
    """
    if getattr(draft, "max_guests", None):
        return int(draft.max_guests), True
    raw = str((parsed_meta or {}).get("guests", "")).strip()
    if raw.isdigit() and 0 < int(raw) <= 30:
        return int(raw), True
    return calculate_max_guests(draft.rooms), False


def build_context(
    *,
    draft,
    description: str,
    object_id: str,
    contacts: dict,
    parsed_meta: dict | None = None,
    complex_name: str | None = None,
    region: str = "Пхукет",
) -> dict:
    """Все подстановки для обоих промптов."""
    max_guests, guests_explicit = resolve_max_guests(draft, parsed_meta)
    amenities = list(draft.amenities or [])
    return {
        "raw_description": description.strip()[:4000],
        "property_type": draft.housing_type or "Жильё",
        "rooms": draft.rooms or "",
        "bathrooms": "",
        "region": region,
        "district": draft.district or region,
        "price_monthly": f"{draft.price_monthly:,.0f}".replace(",", " ") if draft.price_monthly else "",
        "price_yearly": f"{draft.price_yearly:,.0f}".replace(",", " ") if draft.price_yearly else "",
        "deposit": f"{draft.deposit:,.0f} ฿".replace(",", " ") if draft.deposit else "нет данных",
        "amenities_list": ", ".join(amenities) if amenities else "нет данных",
        "max_guests": max_guests,
        "max_guests_explicit": guests_explicit,
        "object_id": object_id,
        "project_name": complex_name or "",
        "view": draft.view or "",
        "top_amenity": amenities[0] if amenities else "",
        "whatsapp_contact": contacts.get("whatsapp", ""),
        "tg_contact": contacts.get("telegram", "ТГ"),
    }


# ---------- шаблонные фолбэки (без LLM) ----------

def template_long(ctx: dict) -> str:
    lines = [f"🏠 {ctx['property_type']}, {ctx['district']}", ""]
    if ctx["rooms"]:
        guests = f", до {ctx['max_guests']} гостей" if ctx["max_guests"] else ""
        lines.append(f"🛏️ {ctx['rooms']} спальни{guests}")
    if ctx["view"]:
        lines.append(f"🌅 Вид: {ctx['view']}")
    amenities = [a.strip() for a in ctx["amenities_list"].split(",") if a.strip()]
    if amenities and ctx["amenities_list"] != "нет данных":
        lines.append(f"✨ {', '.join(amenities[:5])}")
    if ctx["price_monthly"]:
        lines.append(f"💰 {ctx['price_monthly']} ฿/мес")
    elif ctx["price_yearly"]:
        lines.append(f"💰 {ctx['price_yearly']} ฿/год")
    if ctx["deposit"] and ctx["deposit"] != "нет данных":
        lines.append(f"🔐 Залог: {ctx['deposit']}")
    body = ctx["raw_description"].strip()
    if body:
        lines.extend(["", body[:600]])
    if ctx["whatsapp_contact"]:
        lines.extend(["", f"📲 Бронь в WhatsApp {ctx['whatsapp_contact']}"])
    return "\n".join(lines)


def template_social(ctx: dict) -> str:
    region = ctx.get("region", "Пхукет")
    parts = [f"{ctx['property_type']} в Аренду, {region}!"]
    if ctx["project_name"]:
        hook = str(ctx["project_name"]).upper()
        if ctx["rooms"]:
            hook += f" {ctx['rooms']}BR"
        if ctx["view"]:
            hook += f" с видом: {ctx['view']}"
        parts.append(hook + " 🌴")
    if ctx["district"] != region:
        parts.append(f"{ctx['district']}, {region}.")
    else:
        parts.append(f"{region}.")
    if ctx["rooms"]:
        parts.append(f"{ctx['rooms']} спальни, до {ctx['max_guests']} гостей.")
    if ctx["top_amenity"]:
        parts.append(f"{ctx['top_amenity']}.")
    parts.append(f"Больше информации в {ctx['tg_contact']}.")
    parts.append("Цена меняется с сезонностью.")
    if ctx["whatsapp_contact"]:
        parts.append(f"Бронь в WhatsApp {ctx['whatsapp_contact']}")
    return " ".join(parts)


# ---------- публичное API ----------

def generate_long_description(ctx: dict) -> str:
    """Длинный пост (TG/FB): LLM по промпту, иначе шаблон; всегда через валидатор."""
    prompt = fill_template(load_prompt("agent2_description_prompt.txt"), ctx)
    text = call_llm(prompt) or template_long(ctx)
    return validate_and_fit(text, ctx["object_id"])


def generate_social_description(ctx: dict) -> str:
    """Короткий тизер (соц.сети, 280): LLM, иначе шаблон; всегда через валидатор."""
    prompt = fill_template(load_prompt("agent2_social_description_prompt.txt"), ctx)
    text = call_llm(prompt) or template_social(ctx)
    return validate_and_fit_social(text, ctx["object_id"])
