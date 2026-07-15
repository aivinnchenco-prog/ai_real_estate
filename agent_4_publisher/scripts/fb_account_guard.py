#!/usr/bin/env python3
"""Общий предохранитель FB-аккаунта для всех веток автоматизации
(fb_groups_pipeline, fb_marketplace_pipeline).

Один аккаунт — один «человек»: ветки не должны действовать одновременно,
подряд без перерыва или посреди ночи. Настройки: config/fb_account.json,
состояние: data/fb_account/guard.json (общее для веток).

CLI:
  python3 scripts/fb_account_guard.py --status
  python3 scripts/fb_account_guard.py --pause 72          # пауза на N часов
  python3 scripts/fb_account_guard.py --resume            # снять паузу
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PACKAGE_ROOT / "config" / "fb_account.json"

DEFAULTS = {
    "guard_file": "data/fb_account/guard.json",
    "timezone_offset_hours": 7,
    "active_hours": {"start": "09:30", "end": "21:30"},
    "min_minutes_between_sessions": 90,
    "session_gap_jitter_minutes": 45,
}


def load_guard_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


def _guard_path(cfg: dict) -> Path:
    return PACKAGE_ROOT / cfg.get("guard_file", DEFAULTS["guard_file"])


def _load_state(cfg: dict) -> dict:
    path = _guard_path(cfg)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def _save_state(cfg: dict, state: dict) -> None:
    path = _guard_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_hhmm(raw: str) -> tuple[int, int]:
    h, _, m = str(raw).partition(":")
    return int(h), int(m or 0)


def check_account_guard(cfg: dict | None = None) -> str | None:
    """None — можно работать; строка — причина, почему сейчас нельзя."""
    cfg = cfg or load_guard_config()
    state = _load_state(cfg)
    now = _now_utc()

    paused_until = _parse_iso(state.get("paused_until", ""))
    if paused_until and paused_until > now:
        local = paused_until + timedelta(hours=cfg["timezone_offset_hours"])
        reason = state.get("pause_reason", "")
        suffix = f" ({reason})" if reason else ""
        return f"аккаунт на паузе до {local:%d.%m %H:%M} местного{suffix}"

    # Окно активности — по местному времени (Пхукет UTC+7)
    local_now = now + timedelta(hours=cfg["timezone_offset_hours"])
    start_h, start_m = _parse_hhmm(cfg["active_hours"]["start"])
    end_h, end_m = _parse_hhmm(cfg["active_hours"]["end"])
    minutes_now = local_now.hour * 60 + local_now.minute
    if not (start_h * 60 + start_m <= minutes_now <= end_h * 60 + end_m):
        return (
            f"вне окна активности {cfg['active_hours']['start']}–"
            f"{cfg['active_hours']['end']} (сейчас {local_now:%H:%M} местного)"
        )

    # Разрыв между сессиями любых веток: базовый минимум + случайный добавок,
    # зафиксированный при закрытии прошлой сессии (чтобы интервалы не были одинаковыми)
    last_end = _parse_iso(state.get("last_session_end", ""))
    if last_end:
        gap_min = int(state.get("next_gap_minutes") or cfg["min_minutes_between_sessions"])
        ready_at = last_end + timedelta(minutes=gap_min)
        if ready_at > now:
            local_ready = ready_at + timedelta(hours=cfg["timezone_offset_hours"])
            return f"перерыв между FB-сессиями, следующая не раньше {local_ready:%H:%M} местного"

    return None


def record_session_end(cfg: dict | None = None, branch: str = "") -> None:
    """Вызывается веткой после закрытия браузера: фиксируем конец сессии
    и случайный размер перерыва до следующей."""
    cfg = cfg or load_guard_config()
    state = _load_state(cfg)
    base = int(cfg["min_minutes_between_sessions"])
    jitter = int(cfg.get("session_gap_jitter_minutes", 0))
    state["last_session_end"] = _now_utc().isoformat()
    state["last_session_branch"] = branch
    state["next_gap_minutes"] = base + random.randint(0, max(jitter, 0))
    _save_state(cfg, state)


def pause_account(hours: float, reason: str = "") -> None:
    cfg = load_guard_config()
    state = _load_state(cfg)
    state["paused_until"] = (_now_utc() + timedelta(hours=hours)).isoformat()
    state["pause_reason"] = reason
    _save_state(cfg, state)


def resume_account() -> None:
    cfg = load_guard_config()
    state = _load_state(cfg)
    state.pop("paused_until", None)
    state.pop("pause_reason", None)
    _save_state(cfg, state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Предохранитель FB-аккаунта")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--pause", type=float, metavar="HOURS")
    parser.add_argument("--reason", default="", help="Причина паузы (для --pause)")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if args.pause:
        pause_account(args.pause, args.reason)
        print(f"Пауза на {args.pause:g} ч установлена.")
        return 0
    if args.resume:
        resume_account()
        print("Пауза снята.")
        return 0

    reason = check_account_guard()
    print("Можно работать." if reason is None else f"Заблокировано: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
