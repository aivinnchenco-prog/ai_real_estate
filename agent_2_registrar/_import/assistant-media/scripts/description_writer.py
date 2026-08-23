"""Генерация описаний объекта для Notion (Агент 2).

Три колонки:
  «Описание для Telegram» / «Описание для FB Marketplace» — длинный пост
    (промпт config/prompts/agent2_description_prompt.txt, лимит ~964);
  «Описание соц.сети» — тизер для IG/TikTok/FB/Threads/LinkedIn/YouTube
    (промпт agent2_social_description_prompt.txt, лимит 700), с типом аренды
    в первой строке;
  «Описание X.com» — короткий тизер только для X
    (промпт agent2_x_description_prompt.txt, лимит 280).

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
    validate_and_fit_x,
)

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"

_GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"


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


def call_llm(prompt: str) -> str | None:
    """Gemini → None (фолбэк на шаблон решает вызывающий)."""
    return _call_gemini(prompt)


def cjk_ratio(text: str) -> float:
    """Доля CJK-символов (кит./яп./кор.) в тексте."""
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff")
    return cjk / len(text)


def translate_to_russian(text: str) -> str | None:
    """Перевод описания на русский (страховка, когда Airbnb отдал оригинал
    на языке хозяина). None — если LLM недоступен."""
    prompt = (
        "Переведи текст объявления об аренде жилья на русский язык.\n"
        "Сохрани структуру строк, все числа, цены, валюты, названия мест и"
        " HTML-теги (<b>, </b>) без изменений. Не добавляй ничего от себя,"
        " выведи только перевод.\n\n" + text.strip()[:8000]
    )
    result = call_llm(prompt)
    return result.strip() if result else None


# ---------- контекст объекта ----------


def rent_type_label(rent_type: str | None, object_id: str = "") -> str:
    """Краткосрочная аренда / Долгосрочная аренда — для первой строки поста."""
    raw = (rent_type or "").lower()
    oid = (object_id or "").upper()
    if "краткоср" in raw or oid.startswith("A_"):
        return "Краткосрочная аренда"
    if "долгоср" in raw or oid.startswith("F_"):
        return "Долгосрочная аренда"
    return ""


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
    rent_type = getattr(draft, "rent_type", None) or getattr(draft, "rental_type", None) or ""
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
        "rent_type": rent_type,
        "rent_type_label": rent_type_label(str(rent_type), object_id),
        # алиасы ключей, которые ждут промпты
        "project_name": complex_name or "",
        "max_guests": max_guests,
        "top_amenity": amenities[0] if amenities else "",
        "object_id": object_id,
        "rooms": draft.rooms or "",
    }


# ---------- шаблонные фолбэки (без LLM) ----------

def template_long(ctx: dict) -> str:
    # Цену, залог и даты доступности в пост не пишем — обсуждаются в личке.
    lines = [f"🏠 {ctx['property_type']}, {ctx['district']}", ""]
    if ctx["rooms"]:
        guests = f", до {ctx['max_guests']} гостей" if ctx["max_guests"] else ""
        lines.append(f"🛏️ {ctx['rooms']} спальни{guests}")
    if ctx["view"]:
        lines.append(f"🌅 Вид: {ctx['view']}")
    amenities = [a.strip() for a in ctx["amenities_list"].split(",") if a.strip()]
    if amenities and ctx["amenities_list"] != "нет данных":
        lines.append(f"✨ {', '.join(amenities[:5])}")
    body = "\n".join(
        ln for ln in ctx["raw_description"].strip().splitlines()
        if not _is_price_line(ln)
    ).strip()
    if body:
        lines.extend(["", body[:600]])
    return "\n".join(lines)


_PRICE_LINE_RE = re.compile(
    r"^\s*(💰|📅|Цена[:\s]|Залог[:\s])|฿\s*/?\s*мес|помесячно|Доступно с",
    re.IGNORECASE,
)


def _is_price_line(line: str) -> bool:
    return bool(_PRICE_LINE_RE.search(line))


def finalize_long(text: str, ctx: dict) -> str:
    """Код-гарантия правил длинного поста (LLM может ошибиться):
    убрать строки с ценой/датами доступности, добавить CTA в конец."""
    lines = [ln for ln in text.strip().splitlines() if not _is_price_line(ln)]
    tag = f"#{ctx['object_id']}"
    lines = [ln for ln in lines if tag not in ln]  # тег добавим сами в конце
    wa = ctx.get("whatsapp_contact", "")
    if wa:
        cta = f"Пишите в личные сообщения WhatsApp {wa}"
        if wa not in "\n".join(lines):
            lines.extend(["", cta])
    lines.extend(["", tag])
    return "\n".join(lines)


def template_social(ctx: dict) -> str:
    """Тизер для IG/TikTok/FB/Threads — с типом аренды и переносами строк."""
    region = ctx.get("region", "Пхукет")
    lines: list[str] = []
    label = (ctx.get("rent_type_label") or "").strip()
    if label:
        lines.append(label)
        lines.append("")
    lines.append(f"{ctx['property_type']} в аренду, {region}")
    if ctx.get("project_name"):
        hook = str(ctx["project_name"]).upper()
        if ctx.get("rooms"):
            hook += f" {ctx['rooms']}BR"
        if ctx.get("view"):
            hook += f" с видом: {ctx['view']}"
        lines.append(hook)
    if ctx.get("district") and ctx["district"] != region:
        lines.append(f"{ctx['district']}, {region}")
    if ctx.get("rooms"):
        lines.append(f"{ctx['rooms']} спальни, до {ctx['max_guests']} гостей")
    amenities = [a.strip() for a in str(ctx.get("amenities_list") or "").split(",") if a.strip()]
    if amenities and ctx.get("amenities_list") != "нет данных":
        lines.append(", ".join(amenities[:4]))
    elif ctx.get("top_amenity"):
        lines.append(str(ctx["top_amenity"]))
    lines.append("")
    lines.append(f"Подробности в {ctx['tg_contact']}")
    lines.append("Цена меняется с сезонностью.")
    if ctx.get("whatsapp_contact"):
        lines.append(f"Бронь в WhatsApp {ctx['whatsapp_contact']}")
    return "\n".join(lines).strip()


def template_x(ctx: dict) -> str:
    """Плотный тизер 280 символов только для X.com."""
    region = ctx.get("region", "Пхукет")
    parts: list[str] = []
    label = (ctx.get("rent_type_label") or "").strip()
    if label:
        parts.append(f"{label}.")
    parts.append(f"{ctx['property_type']} в аренду, {region}!")
    if ctx.get("project_name"):
        hook = str(ctx["project_name"]).upper()
        if ctx.get("rooms"):
            hook += f" {ctx['rooms']}BR"
        if ctx.get("view"):
            hook += f" с видом: {ctx['view']}"
        parts.append(hook + " 🌴")
    if ctx.get("district") and ctx["district"] != region:
        parts.append(f"{ctx['district']}, {region}.")
    elif not ctx.get("project_name"):
        parts.append(f"{region}.")
    if ctx.get("rooms"):
        parts.append(f"{ctx['rooms']} спальни, до {ctx['max_guests']} гостей.")
    if ctx.get("top_amenity"):
        parts.append(f"{ctx['top_amenity']}.")
    parts.append(f"Подробности в {ctx['tg_contact']}.")
    parts.append("Цена меняется с сезонностью.")
    if ctx.get("whatsapp_contact"):
        parts.append(f"WhatsApp {ctx['whatsapp_contact']}")
    return " ".join(parts)


# ---------- публичное API ----------

def generate_long_description(ctx: dict) -> str:
    """Длинный пост (TG/FB): LLM по промпту, иначе шаблон; всегда через валидатор."""
    prompt = fill_template(load_prompt("agent2_description_prompt.txt"), ctx)
    text = call_llm(prompt) or template_long(ctx)
    return validate_and_fit(finalize_long(text, ctx), ctx["object_id"])


def generate_social_description(ctx: dict) -> str:
    """Тизер для соцсетей кроме X (лимит 700): LLM, иначе шаблон; всегда через валидатор."""
    prompt = fill_template(load_prompt("agent2_social_description_prompt.txt"), ctx)
    text = call_llm(prompt) or template_social(ctx)
    return validate_and_fit_social(text, ctx["object_id"])


def generate_x_description(ctx: dict) -> str:
    """Короткий тизер для X.com (лимит 280): LLM, иначе шаблон; всегда через валидатор."""
    prompt = fill_template(load_prompt("agent2_x_description_prompt.txt"), ctx)
    text = call_llm(prompt) or template_x(ctx)
    return validate_and_fit_x(text, ctx["object_id"])
