"""Одноразовая настройка amoCRM: воронка «Аренда — лиды» + кастомные поля сделки.

Запуск:  python3 scripts/setup_amo.py
Повторный запуск безопасен: существующие воронка/поля не дублируются.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v.strip())

from agent6_qualifier.amo import AmoClient  # noqa: E402


def main() -> int:
    amo = AmoClient()
    try:
        stages = amo.ensure_pipeline()
    except Exception as e:
        print(f"ОШИБКА доступа к amoCRM: {e}")
        print("Проверьте AMO_SUBDOMAIN и AMO_ACCESS_TOKEN в .env "
              "(нужен долгосрочный токен интеграции, обычно длинный JWT, начинается с eyJ).")
        return 1

    print(f"Воронка «{amo.__class__ and 'Аренда — лиды'}» готова. Стадии:")
    for name, sid in stages.items():
        print(f"  {sid}  {name}")

    fields = amo.ensure_lead_fields()
    print("\nПоля сделки:")
    for name, fid in fields.items():
        print(f"  {fid}  {name}")
    print("\nГотово. ID стадий и полей код получает динамически при старте.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
