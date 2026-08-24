#!/usr/bin/env python3
"""Район (латиница центроидов) → Notion Select «Зона».

СИНХРОНИЗИРОВАТЬ с Site Openhome src/lib/zones.ts при изменении.
Справочник: config/zones_mapping.json. Неизвестное / Kathu / «Phuket» → None
(поле «Зона» не выдумываем).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "zones_mapping.json"

_SPACE_RE = re.compile(r"\s+")

WriteAction = Literal["set", "clear", "omit"]


def normalize_district(value: str | None) -> str:
    return _SPACE_RE.sub(" ", (value or "").strip().lower())


@lru_cache(maxsize=1)
def load_zones_mapping() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def resolve_zone(district: str | None) -> str | None:
    """Точное имя Select-опции «Зона» или None, если маппить нельзя."""
    key = normalize_district(district)
    if not key:
        return None
    mapping = load_zones_mapping()
    aliases: dict[str, str] = mapping.get("aliases") or {}
    zone = aliases.get(key)
    if not zone:
        return None
    allowed = set(mapping.get("zones") or [])
    return zone if zone in allowed else None


def should_write_zone(
    *,
    zone: str | None,
    is_new_page: bool,
    old_district: str = "",
    new_district: str = "",
    old_zone: str | None = None,
) -> WriteAction:
    """set — записать Select; clear — сбросить; omit — не трогать поле.

    Новая страница: пишем только если зона известна.
    Район не изменился: дозаполняем пустую Зону, уже заполненную не перезаписываем.
    Район изменился: ставим новую зону или очищаем, если маппинга нет.
    """
    if is_new_page:
        return "set" if zone else "omit"
    if normalize_district(old_district) != normalize_district(new_district):
        return "set" if zone else "clear"
    if not old_zone and zone:
        return "set"
    return "omit"


def apply_zone_to_properties(
    properties: dict[str, Any],
    *,
    field_name: str,
    zone: str | None,
    is_new_page: bool,
    old_district: str = "",
    new_district: str = "",
    old_zone: str | None = None,
) -> WriteAction:
    action = should_write_zone(
        zone=zone,
        is_new_page=is_new_page,
        old_district=old_district,
        new_district=new_district,
        old_zone=old_zone,
    )
    if action == "set" and zone:
        properties[field_name] = {"select": {"name": zone}}
    elif action == "clear":
        properties[field_name] = {"select": None}
    return action
