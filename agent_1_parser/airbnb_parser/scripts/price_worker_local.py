#!/usr/bin/env python3
"""Локальный воркер цен (Mac): **fallback / manual recovery** для monthly_prices.

Основной путь — server-side background queue (`pricing_worker.py`).
Этот скрипт остаётся для ручного добора и диагностики с домашнего IP.

Usage:
  .venv/bin/python scripts/price_worker_local.py --watch
  .venv/bin/python scripts/price_worker_local.py --once
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path
from queue import Queue

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from agent2_handoff import price_months_ahead, find_agent2_root  # noqa: E402
from availability import fetch_calendar_days  # noqa: E402
from airbnb_parser import AirbnbParser  # noqa: E402
from monthly_pricing import (  # noqa: E402
    collect_monthly_prices,
    collect_monthly_prices_parallel,
    entry_has_price,
)
from price_cache import load_price_cache, save_price_cache  # noqa: E402
from CustomLogger import logger  # noqa: E402

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
FIELD_OBJECT_ID = "Объект ID"
FIELD_SOURCE = "Источник объявления"
FIELD_MONTHLY = "monthly_prices"
FIELD_PRICE_MONTH = "Цена за месяц"
STATE_FILE = ROOT / "data" / "price_worker_state.json"
# Минимум месяцев с ценой в Notion; меньше — добираем (сид с VPS часто даёт 1).
MIN_MONTHS = max(1, int(os.getenv("PRICE_MIN_MONTHS", "3")))
_CHIP_RE = re.compile(
    r"^(?P<month>\d{4}-\d{2})\s*(?P<sep>[·≈])\s*(?P<price>[\d\s\xa0]+)\s*฿"
)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('NOTION_API_KEY', '')}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(url: str, payload: dict | None = None, method: str = "POST") -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers())
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _plain_url(prop: dict | None) -> str:
    if not prop:
        return ""
    if prop.get("type") == "url":
        return (prop.get("url") or "").strip()
    texts = prop.get("rich_text") or []
    return "".join(t.get("plain_text", "") for t in texts).strip()


def _object_id(page: dict) -> str:
    prop = page.get("properties", {}).get(FIELD_OBJECT_ID) or {}
    texts = prop.get("rich_text") or []
    return "".join(t.get("plain_text", "") for t in texts).strip()


def _monthly_chips(page: dict) -> list[str]:
    prop = page.get("properties", {}).get(FIELD_MONTHLY) or {}
    if prop.get("type") != "multi_select":
        return []
    return [x.get("name") or "" for x in (prop.get("multi_select") or []) if x.get("name")]


def _monthly_empty(page: dict) -> bool:
    return len(_monthly_chips(page)) == 0


def _needs_more_prices(page: dict, min_months: int = MIN_MONTHS) -> bool:
    return len(_monthly_chips(page)) < min_months


def parse_monthly_chips(chips: list[str]) -> dict:
    """Обратный разбор чипов Notion → monthly_prices dict."""
    out: dict = {}
    for name in chips:
        m = _CHIP_RE.match((name or "").strip())
        if not m:
            continue
        try:
            price = int(m.group("price").replace(" ", "").replace("\xa0", ""))
        except ValueError:
            continue
        out[m.group("month")] = {
            "price": price,
            "status": "prorated" if m.group("sep") == "≈" else "monthly",
            "source": "notion_chip",
        }
    return out


def monthly_price_options(monthly: dict) -> list[str]:
    options = []
    for month in sorted(monthly):
        entry = monthly.get(month) or {}
        price = entry.get("price")
        if not price:
            continue
        sep = "≈" if entry.get("status") == "prorated" else "·"
        pretty = f"{int(price):,}".replace(",", " ")
        options.append(f"{month} {sep} {pretty} ฿")
    return options


def first_upcoming_price(monthly: dict, today: date | None = None) -> float | None:
    today = today or date.today()
    current = f"{today.year:04d}-{today.month:02d}"
    for month in sorted(monthly):
        if month <= current:
            continue
        entry = monthly.get(month) or {}
        if entry.get("price"):
            return float(entry["price"])
    return None


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"done": {}, "failed": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def find_pending(limit: int = 50, min_months: int = MIN_MONTHS) -> list[dict]:
    """Объекты Airbnb, у которых меньше min_months цен в monthly_prices."""
    db_id = os.getenv("NOTION_DB_ID") or os.getenv("NOTION_DATABASE_ID", "")
    data = _request(
        f"{NOTION_API}/databases/{db_id}/query",
        {
            "page_size": min(100, max(limit, 50)),
            "sorts": [{"timestamp": "created_time", "direction": "descending"}],
        },
    )
    out = []
    for idx, page in enumerate(data.get("results", [])):
        oid = _object_id(page)
        src = _plain_url(page.get("properties", {}).get(FIELD_SOURCE))
        if not oid or "airbnb." not in src.lower():
            continue
        if not _needs_more_prices(page, min_months):
            continue
        chips = _monthly_chips(page)
        out.append(
            {
                "page_id": page["id"],
                "object_id": oid,
                "source_url": src,
                "existing": parse_monthly_chips(chips),
                "chip_count": len(chips),
                "ord": idx,  # newer = smaller
            }
        )
    # 1) почти готовые (1..min-1 чипов) — новее выше
    # 2) полностью пустые — новее выше
    # Иначе 1 месяц с VPS-сида навсегда ждёт за хвостом пустых.
    out.sort(
        key=lambda x: (
            0 if 0 < x["chip_count"] < min_months else 1,
            x["ord"],
        )
    )
    return out[:limit]


def write_prices_to_notion(page_id: str, monthly: dict) -> None:
    options = monthly_price_options(monthly)
    props: dict = {}
    if options:
        props[FIELD_MONTHLY] = {
            "multi_select": [{"name": name} for name in options]
        }
    upcoming = first_upcoming_price(monthly)
    if upcoming is not None:
        props[FIELD_PRICE_MONTH] = {"number": upcoming}
    if not props:
        raise RuntimeError("нет цен для записи в Notion")
    _request(f"{NOTION_API}/pages/{page_id}", {"properties": props}, method="PATCH")


def collect_for_url(url: str, existing: dict | None = None) -> dict:
    """Сбор цен: календарь один раз; workers=1 — один браузер подряд (стабильнее)."""
    months = max(MIN_MONTHS, price_months_ahead(find_agent2_root()) or 12)
    workers = max(1, int(getattr(config, "PRICE_PARALLEL_WORKERS", 3)))
    cached = load_price_cache(url)
    base = dict(cached or {})
    for key, entry in (existing or {}).items():
        if entry_has_price(entry):
            base[key] = entry

    t0 = time.perf_counter()
    availability = fetch_calendar_days(url)
    have = sum(1 for v in base.values() if entry_has_price(v))
    logger.info(
        f"локальные цены: parallel×{workers}, months_ahead={months}, уже есть {have}"
    )

    if workers <= 1:
        parser = AirbnbParser(headless=True)
        try:
            prices = collect_monthly_prices(
                lambda check_in, check_out: parser.fetch_price_for_period(
                    url, check_in, check_out
                ),
                availability,
                months_ahead=months,
                min_segment_days=config.PRICE_MIN_SEGMENT_DAYS,
                existing=base,
                only_missing=True,
            )
        finally:
            try:
                parser.close()
            except Exception:
                pass
    else:
        parsers: list[AirbnbParser] = []
        pool: Queue = Queue()
        for _ in range(workers):
            p = AirbnbParser(headless=True)
            parsers.append(p)
            pool.put(p)

        def make_worker():
            parser = pool.get()

            def fetch(check_in, check_out):
                return parser.fetch_price_for_period(url, check_in, check_out)

            def release():
                pool.put(parser)

            return fetch, release

        try:
            prices = collect_monthly_prices_parallel(
                make_worker,
                availability,
                months_ahead=months,
                min_segment_days=config.PRICE_MIN_SEGMENT_DAYS,
                existing=base,
                only_missing=True,
                workers=workers,
            )
        finally:
            for p in parsers:
                try:
                    p.close()
                except Exception:
                    pass

    sec = round(time.perf_counter() - t0, 1)
    ok = sum(1 for v in prices.values() if entry_has_price(v))
    logger.info(f"локальные цены: {ok}/{len(prices)} за {sec}с (×{workers}) — {url[:60]}")
    save_price_cache(url, prices)
    return prices


def _skip_failed_recently(state: dict, oid: str, cooldown_sec: int = 1800) -> bool:
    """Не долбим один объект подряд, если только что упал (иначе блокирует очередь)."""
    raw = (state.get("failed") or {}).get(oid)
    if not raw:
        return False
    # failed может быть строкой (старый формат) или {at, reason}
    if isinstance(raw, dict):
        at = raw.get("at") or ""
        try:
            ts = time.mktime(time.strptime(at, "%Y-%m-%dT%H:%M:%S"))
        except (TypeError, ValueError):
            return False
        return (time.time() - ts) < cooldown_sec
    return False


def process_one(item: dict, state: dict, *, min_months: int = MIN_MONTHS) -> bool:
    oid = item["object_id"]
    prev = state.get("done", {}).get(oid) or {}
    if prev.get("months", 0) >= min_months:
        return False
    if _skip_failed_recently(state, oid):
        logger.info(f"{oid}: пропуск (недавний fail, cooldown)")
        return False
    logger.info(
        f"=== price worker: {oid} (chips={item.get('chip_count', 0)}, min={min_months}) ==="
    )
    try:
        prices = collect_for_url(item["source_url"], existing=item.get("existing") or {})
        ok = sum(1 for v in prices.values() if entry_has_price(v))
        if ok == 0:
            state.setdefault("failed", {})[oid] = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "reason": "no prices",
            }
            save_state(state)
            logger.warning(f"{oid}: Airbnb не вернул цены")
            return False
        write_prices_to_notion(item["page_id"], prices)
        state.setdefault("failed", {}).pop(oid, None)
        if ok >= min_months:
            state.setdefault("done", {})[oid] = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "months": ok,
            }
        else:
            state.setdefault("partial", {})[oid] = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "months": ok,
            }
        save_state(state)
        logger.info(f"{oid}: цены записаны в Notion ({ok} мес., min={min_months})")
        return True
    except Exception as exc:
        state.setdefault("failed", {})[oid] = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "reason": str(exc)[:300],
        }
        save_state(state)
        logger.error(f"{oid}: {exc}")
        return False


def _run_item_subprocess(item: dict, min_months: int, timeout_sec: int = 900) -> int:
    """Отдельный процесс на объект: падение Chrome не убивает watch-цикл."""
    import subprocess

    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--once",
        "--min-months",
        str(min_months),
        "--object-id",
        item["object_id"],
        "--inprocess",
    ]
    logger.info(f"subprocess {item['object_id']} timeout={timeout_sec}s")
    try:
        return subprocess.call(cmd, cwd=str(ROOT), timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        logger.error(f"{item['object_id']}: timeout {timeout_sec}s — убиваем")
        return 124


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="поллинг Notion")
    parser.add_argument("--once", action="store_true", help="один проход")
    parser.add_argument("--interval", type=int, default=20, help="сек между опросами")
    parser.add_argument("--object-id", default="", help="только этот Объект ID")
    parser.add_argument(
        "--min-months",
        type=int,
        default=MIN_MONTHS,
        help=f"минимум месяцев с ценой (default {MIN_MONTHS})",
    )
    parser.add_argument(
        "--inprocess",
        action="store_true",
        help="обрабатывать в этом процессе (для subprocess-воркера)",
    )
    args = parser.parse_args()

    if not os.getenv("NOTION_API_KEY"):
        print("NOTION_API_KEY не задан в .env Агента 1", file=sys.stderr)
        return 1
    if not config.PRICE_COLLECT_ENABLED:
        print(
            "PRICE_COLLECT_ENABLED=0 — на Mac для воркера должно быть true "
            "(цены собираем здесь).",
            file=sys.stderr,
        )
        return 1

    min_months = max(1, int(args.min_months))
    state = load_state()
    if args.watch:
        logger.info(
            f"price worker watch каждые {args.interval}s, min_months={min_months} "
            f"(subprocess per object)"
        )
        while True:
            pending = find_pending(min_months=min_months)
            if args.object_id:
                pending = [x for x in pending if x["object_id"] == args.object_id]
            # cooldown на failed
            pending = [x for x in pending if not _skip_failed_recently(state, x["object_id"])]
            for item in pending[:5]:
                code = _run_item_subprocess(item, min_months)
                state = load_state()  # дочерний процесс мог обновить state
                if code != 0:
                    state.setdefault("failed", {})[item["object_id"]] = {
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "reason": f"subprocess exit {code}",
                    }
                    save_state(state)
            time.sleep(max(5, args.interval))
    else:
        pending = find_pending(min_months=min_months)
        if args.object_id:
            pending = [x for x in pending if x["object_id"] == args.object_id]
        if not pending:
            print(f"Нет объектов Airbnb с < {min_months} ценами в monthly_prices")
            return 0
        for item in pending:
            process_one(item, state, min_months=min_months)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
