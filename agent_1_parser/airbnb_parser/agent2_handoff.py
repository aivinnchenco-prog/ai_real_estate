"""Передача спарсенного Airbnb-объекта Агенту 2 (Notion-пайплайн).

Вместо Google Sheets/Drive: собираем сессию в формате Агента 2
(data/sessions/A_YYYYMMDD_HHMMSS/ с description.txt, photos/, parsed.json)
и запускаем agent2_structurize.py, который создаёт объект в Notion CRM
и грузит фото в R2.

parsed.json несёт всё, что Агент 2 не вытащит из текста:
source="AIRBNB" (префикс A_ для Объект ID), координаты карты (точка Google
Maps + Район), владелец/агентство с контактами, цены по месяцам.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config
from CustomLogger import logger


@dataclass
class HandoffResult:
    session_id: str = ""
    session_path: str = ""
    agent2_ran: bool = False
    agent2_ok: bool = False
    object_id: str = ""
    note: str = ""
    monthly_prices: dict = field(default_factory=dict)
    owner: dict = field(default_factory=dict)


def find_agent2_root() -> Path | None:
    """Корень Агента 2 (assistant-media): AGENT2_ROOT из env или авто-поиск в монорепе."""
    if config.AGENT2_ROOT:
        p = Path(config.AGENT2_ROOT).expanduser()
        if (p / "scripts" / "agent2_structurize.py").exists():
            return p
        logger.warning(f"AGENT2_ROOT задан, но agent2_structurize.py не найден: {p}")
        return None

    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "agent_2_registrar" / "_import" / "assistant-media"
        if (candidate / "scripts" / "agent2_structurize.py").exists():
            return candidate
    return None


def price_months_ahead(agent2_root: Path | None) -> int:
    """PRICE_MONTHS_AHEAD: env приоритетнее, иначе config/pipeline.json Агента 2."""
    if config.PRICE_MONTHS_AHEAD is not None:
        return config.PRICE_MONTHS_AHEAD
    if agent2_root:
        try:
            cfg = json.loads((agent2_root / "config" / "pipeline.json").read_text(encoding="utf-8"))
            return int(cfg.get("price_months_ahead", 12))
        except (OSError, ValueError):
            pass
    return 12


def new_session_id() -> str:
    return datetime.now().strftime("A_%Y%m%d_%H%M%S")


def build_description(message_text: str, listing_data: dict, url: str) -> str:
    """description.txt для Агента 2 — человекочитаемый текст объявления."""
    parts = [message_text.strip()] if message_text else []
    description = (listing_data.get("Описание") or "").strip()
    if description and description not in (message_text or ""):
        parts.append(description)
    parts.append(f"Источник: {url}")
    return "\n\n".join(p for p in parts if p)


def build_session(
    *,
    agent2_root: Path,
    url: str,
    message_text: str,
    listing_data: dict,
    image_paths: list[str],
    monthly_prices: dict | None = None,
    owner: dict | None = None,
) -> tuple[str, Path]:
    """Создаёт сессию в data/sessions Агента 2. Возвращает (session_id, path)."""
    session_id = new_session_id()
    session_path = agent2_root / "data" / "sessions" / session_id
    photos_dir = session_path / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    (session_path / "description.txt").write_text(
        build_description(message_text, listing_data, url), encoding="utf-8"
    )

    for i, src in enumerate(image_paths, 1):
        src_path = Path(src)
        if not src_path.exists():
            continue
        suffix = src_path.suffix.lower() or ".jpg"
        shutil.copy2(src_path, photos_dir / f"photo_{i:03d}{suffix}")

    location = listing_data.get("Локация") or {}
    payload = {
        "source": "AIRBNB",
        "source_url": url,
        "parsed_at": datetime.now().isoformat(timespec="seconds"),
        "title": listing_data.get("Название") or listing_data.get("Название_2") or "",
        "price": listing_data.get("Цена", ""),
        "price_display": listing_data.get("Цена_отображение", ""),
        "guests": listing_data.get("Гостей", ""),
        "location": {
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "subtitle": location.get("subtitle", ""),
        },
        "host": listing_data.get("Хозяин") or {},
        "owner": owner or {},
        "monthly_prices": monthly_prices or {},
        "prices_deferred": not getattr(config, "PRICE_COLLECT_ENABLED", True),
        "calendar_url": url,
    }
    (session_path / "parsed.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(f"Agent2 handoff: сессия {session_id} "
                f"({len(image_paths)} фото) → {session_path}")
    return session_id, session_path


def object_id_from_session(agent2_root: Path, session_id: str) -> str:
    """object_id из session.json после structurize (надёжнее regex по stdout)."""
    path = agent2_root / "data" / "sessions" / session_id / "session.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (data.get("object_id") or "").strip()
    except (OSError, json.JSONDecodeError, TypeError):
        return ""


def object_id_from_agent2_output(output: str) -> str:
    """Достаёт object_id из JSON stdout agent2_structurize; иначе — regex без session timestamp."""
    marker = '"object_id"'
    idx = output.find(marker)
    if idx >= 0:
        start = output.rfind("{", 0, idx)
        if start >= 0:
            depth = 0
            for i in range(start, len(output)):
                ch = output[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(output[start : i + 1])
                            oid = (data.get("object_id") or "").strip()
                            if oid:
                                return oid
                        except json.JSONDecodeError:
                            pass
                        break
    # A_20260724_003 / F_20260724_012 — не путаем с session A_20260724_180630 (HHMMSS).
    for m in re.finditer(r"\b([FA]_\d{8}_(\d{3,4}))\b", output):
        return m.group(1)
    return ""


def resolve_object_id(agent2_root: Path, session_id: str, output: str = "") -> str:
    """Лучший доступный object_id после Агента 2."""
    oid = object_id_from_session(agent2_root, session_id)
    if oid:
        return oid
    if output:
        oid = object_id_from_agent2_output(output)
        if oid:
            return oid
    return ""


def run_agent2(agent2_root: Path, session_id: str, source_url: str, timeout: int = 900) -> tuple[bool, str]:
    """Запускает agent2_structurize.py. Возвращает (ok, object_id)."""
    cmd = [
        sys.executable,
        str(agent2_root / "scripts" / "agent2_structurize.py"),
        "--session", session_id,
        "--source", source_url,
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(agent2_root), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        logger.error(f"Agent2 structurize timeout ({timeout}s) для {session_id}")
        return False, ""

    output = (proc.stdout or "") + (proc.stderr or "")
    object_id = resolve_object_id(agent2_root, session_id, output)

    if proc.returncode != 0:
        logger.error(f"Agent2 structurize failed ({proc.returncode}): {output[-1500:]}")
        return False, object_id

    logger.info(f"Agent2 structurize OK: {session_id} → {object_id or '(id в Notion)'}")
    return True, object_id


def handoff_to_agent2(
    *,
    url: str,
    message_text: str,
    listing_data: dict,
    image_paths: list[str],
    monthly_prices: dict | None = None,
    owner: dict | None = None,
    autorun: bool | None = None,
) -> HandoffResult:
    """Полная передача: сессия + (опционально) запуск структуризации."""
    result = HandoffResult(monthly_prices=monthly_prices or {}, owner=owner or {})

    agent2_root = find_agent2_root()
    if agent2_root is None:
        result.note = "Агент 2 не найден (задай AGENT2_ROOT в .env)"
        logger.error(result.note)
        return result

    session_id, session_path = build_session(
        agent2_root=agent2_root,
        url=url,
        message_text=message_text,
        listing_data=listing_data,
        image_paths=image_paths,
        monthly_prices=monthly_prices,
        owner=owner,
    )
    result.session_id = session_id
    result.session_path = str(session_path)

    if autorun is None:
        autorun = config.AGENT2_AUTORUN
    if autorun:
        result.agent2_ran = True
        result.agent2_ok, result.object_id = run_agent2(agent2_root, session_id, url)
        if result.agent2_ok:
            result.note = f"📇 Notion: {result.object_id or session_id}"
        else:
            result.note = (f"⚠️ Сессия {session_id} собрана, но структуризация упала — "
                           f"запусти вручную: agent2_structurize.py --session {session_id}")
    else:
        result.note = f"📦 Сессия {session_id} готова для Агента 2 (автозапуск выключен)"

    return result
