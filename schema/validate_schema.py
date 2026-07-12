#!/usr/bin/env python3
"""Валидатор схемы Notion-таблицы против контракта schema/notion_schema.json.

Тянет живую схему через Notion API (env: NOTION_API_KEY, NOTION_DB_ID),
сравнивает с контрактом и печатает расхождения тремя списками:
  MISSING       — в контракте есть, в таблице нет (planned-колонки дают только warning)
  EXTRA         — в таблице есть, в контракте нет
  TYPE_MISMATCH — колонка есть в обоих, но тип отличается

Ненулевой exit code при MISSING или TYPE_MISMATCH.

Запуск:  python3 schema/validate_schema.py [--skip-schema-check] [--schema PATH]
Из кода: from validate_schema import run_validation; ok = run_validation()
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import requests  # type: ignore

    def _fetch_json(url: str, headers: dict) -> dict:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

except ImportError:  # requests не установлен — хватает стандартной библиотеки
    import urllib.request

    def _fetch_json(url: str, headers: dict) -> dict:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)


NOTION_API_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "notion_schema.json"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _resolve_credentials() -> tuple[str, str]:
    # Фолбэк на корневой .env репозитория, чтобы валидатор работал из любого агента.
    if not (os.environ.get("NOTION_API_KEY") and os.environ.get("NOTION_DB_ID")):
        _load_env_file(Path(__file__).resolve().parents[1] / ".env")
    api_key = os.environ.get("NOTION_API_KEY", "")
    db_id = os.environ.get("NOTION_DB_ID") or os.environ.get("NOTION_DATABASE_ID", "")
    return api_key, db_id


def fetch_live_schema(api_key: str, db_id: str) -> dict[str, str]:
    """Имя колонки -> тип (как в Notion API)."""
    data = _fetch_json(
        f"{NOTION_API_URL}/databases/{db_id}",
        {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_VERSION,
        },
    )
    return {name: prop.get("type", "?") for name, prop in data.get("properties", {}).items()}


def load_contract(schema_path: Path) -> list[dict]:
    with schema_path.open(encoding="utf-8") as f:
        return json.load(f)["columns"]


def compare(contract: list[dict], live: dict[str, str]) -> dict[str, list[str]]:
    missing: list[str] = []
    planned_missing: list[str] = []
    type_mismatch: list[str] = []

    contract_names = set()
    for col in contract:
        name = col["exact_name"]
        contract_names.add(name)
        expected_type = col["type"]
        if name not in live:
            if col.get("planned"):
                planned_missing.append(name)
            else:
                missing.append(name)
        elif live[name] != expected_type:
            type_mismatch.append(f"{name}: contract={expected_type}, table={live[name]}")

    extra = sorted(name for name in live if name not in contract_names)
    return {
        "missing": sorted(missing),
        "planned_missing": sorted(planned_missing),
        "extra": extra,
        "type_mismatch": sorted(type_mismatch),
    }


def run_validation(schema_path: Path | None = None, *, verbose: bool = True) -> bool:
    """True — схема валидна (нет MISSING и TYPE_MISMATCH)."""
    schema_path = schema_path or DEFAULT_SCHEMA_PATH
    api_key, db_id = _resolve_credentials()
    if not api_key or not db_id:
        print(
            "[schema] ERROR: NOTION_API_KEY / NOTION_DB_ID не заданы (env или корневой .env)",
            file=sys.stderr,
        )
        return False

    try:
        live = fetch_live_schema(api_key, db_id)
    except Exception as exc:  # сетевые/HTTP ошибки — валидацию считаем проваленной
        print(f"[schema] ERROR: не удалось получить схему из Notion: {exc}", file=sys.stderr)
        return False

    result = compare(load_contract(schema_path), live)

    if verbose or result["missing"] or result["type_mismatch"]:
        print(f"[schema] Таблица: {len(live)} колонок, контракт: {schema_path}")
        _print_list("MISSING (в контракте есть, в таблице нет)", result["missing"])
        _print_list("EXTRA (в таблице есть, в контракте нет)", result["extra"])
        _print_list("TYPE_MISMATCH", result["type_mismatch"])
        if result["planned_missing"]:
            print(f"[schema] WARNING planned-колонки ещё не созданы: {', '.join(result['planned_missing'])}")

    ok = not result["missing"] and not result["type_mismatch"]
    print(f"[schema] {'OK' if ok else 'FAILED'}")
    return ok


def _print_list(title: str, items: list[str]) -> None:
    print(f"[schema] {title}: {len(items)}")
    for item in items:
        print(f"  - {item}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-schema-check", action="store_true", help="Пропустить валидацию")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH, help="Путь к notion_schema.json")
    args = parser.parse_args()

    if args.skip_schema_check:
        print("[schema] SKIPPED (--skip-schema-check)")
        return 0
    return 0 if run_validation(args.schema) else 1


if __name__ == "__main__":
    sys.exit(main())
