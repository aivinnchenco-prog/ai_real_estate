#!/usr/bin/env python3
"""Ежедневный отчёт FB Groups / Marketplace в Telegram-бот ошибок.

Окно: календарный день Пхукета на 18:00 Asia/Bangkok (постинг ещё может идти до 21:30).

  python3 scripts/fb_daily_report.py              # печать, не слать
  python3 scripts/fb_daily_report.py --send
  python3 scripts/fb_daily_report.py --date 2026-08-21 --send
  python3 scripts/fb_daily_report.py --send --if-bangkok-18
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_pipeline as pp  # noqa: E402

BANGKOK = ZoneInfo("Asia/Bangkok")
UTC = timezone.utc


def load_posts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    posts = data.get("posts") if isinstance(data, dict) else None
    return list(posts) if isinstance(posts, list) else []


def parse_ts(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def report_day(now: datetime | None = None, override: date | None = None) -> date:
    """Календарный день Пхукета для отчёта."""
    if override:
        return override
    now_bkk = (now or datetime.now(UTC)).astimezone(BANGKOK)
    # До 09:00 по Пхукету вчерашний день ещё «полный» (окно постинга не началось).
    if now_bkk.hour < 9:
        return (now_bkk - timedelta(days=1)).date()
    return now_bkk.date()


def day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=BANGKOK)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def in_window(post: dict[str, Any], start: datetime, end: datetime) -> bool:
    ts = parse_ts(post.get("ts"))
    return bool(ts and start <= ts < end)


def group_label(url: str) -> str:
    slug = (url or "").rstrip("/").split("/")[-1]
    return slug or (url or "—")


def filter_posts(posts: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    return [p for p in posts if in_window(p, start, end)]


def summarize_groups(posts: list[dict[str, Any]]) -> dict[str, Any]:
    by_object: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unique_groups: set[str] = set()
    with_url = 0
    for post in posts:
        oid = str(post.get("object_id") or "?").strip() or "?"
        by_object[oid].append(post)
        key = (post.get("group") or "").rstrip("/").lower()
        if key:
            unique_groups.add(key)
        if str(post.get("post_url") or "").strip():
            with_url += 1
    objects = []
    for oid, rows in sorted(by_object.items()):
        urls = sum(1 for r in rows if str(r.get("post_url") or "").strip())
        times = [t for t in (parse_ts(r.get("ts")) for r in rows) if t]
        first = min(times) if times else None
        last = max(times) if times else None
        objects.append(
            {
                "object_id": oid,
                "posts": len(rows),
                "with_url": urls,
                "first": first.astimezone(BANGKOK) if first else None,
                "last": last.astimezone(BANGKOK) if last else None,
                "groups": [group_label(str(r.get("group") or "")) for r in rows],
            }
        )
    return {
        "posts": len(posts),
        "objects": objects,
        "unique_groups": len(unique_groups),
        "with_url": with_url,
    }


def summarize_marketplace(posts: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for post in sorted(posts, key=lambda p: parse_ts(p.get("ts")) or datetime.min.replace(tzinfo=UTC)):
        ts = parse_ts(post.get("ts"))
        url = str(post.get("post_url") or "").strip()
        items.append(
            {
                "object_id": str(post.get("object_id") or "?").strip() or "?",
                "ts": ts.astimezone(BANGKOK) if ts else None,
                "has_url": bool(url),
            }
        )
    return {"posts": len(items), "items": items}


def fmt_hm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else "—"


def ru_plural(n: int, one: str, few: str, many: str) -> str:
    nabs = abs(n) % 100
    if 11 <= nabs <= 14:
        return many
    nabs = nabs % 10
    if nabs == 1:
        return one
    if 2 <= nabs <= 4:
        return few
    return many


def format_report(
    day: date,
    groups: dict[str, Any],
    marketplace: dict[str, Any],
    *,
    queue_groups: list[str] | None = None,
    queue_marketplace: list[str] | None = None,
) -> str:
    lines = [
        f"FB отчёт за {day.strftime('%d.%m.%Y')} (Пхукет)",
        f"на 18:00 Бангкок · окно постинга 09:30–21:30",
        "",
    ]
    g_objects = groups.get("objects") or []
    n_posts = int(groups.get("posts") or 0)
    n_unique = int(groups.get("unique_groups") or 0)
    lines.append(
        f"Группы: {n_posts} {ru_plural(n_posts, 'пост', 'поста', 'постов')} · "
        f"{len(g_objects)} {ru_plural(len(g_objects), 'объект', 'объекта', 'объектов')} · "
        f"{n_unique} {ru_plural(n_unique, 'группа', 'группы', 'групп')}"
        + (f" · {groups['with_url']} со ссылкой" if groups.get("with_url") else "")
    )
    if not g_objects:
        lines.append("• сегодня в группы ничего не ушло")
    else:
        for row in g_objects:
            extra = f", {row['with_url']} со ссылкой" if row["with_url"] else ""
            n = int(row["posts"])
            lines.append(
                f"• {row['object_id']} — {n} {ru_plural(n, 'группа', 'группы', 'групп')} "
                f"({fmt_hm(row['first'])}–{fmt_hm(row['last'])}{extra})"
            )

    lines.append("")
    mp = marketplace.get("items") or []
    n_mp = int(marketplace.get("posts") or 0)
    lines.append(
        f"Marketplace: {n_mp} {ru_plural(n_mp, 'объявление', 'объявления', 'объявлений')}"
    )
    if not mp:
        lines.append("• объявлений не было")
    else:
        for row in mp:
            mark = "ссылка есть" if row["has_url"] else "url не найден"
            lines.append(f"• {row['object_id']} — {fmt_hm(row['ts'])} ({mark})")

    if queue_groups is not None or queue_marketplace is not None:
        lines.append("")
        gq = queue_groups or []
        mq = queue_marketplace or []
        lines.append("Очередь групп: " + (", ".join(gq) if gq else "пусто"))
        lines.append("Очередь Marketplace: " + (", ".join(mq) if mq else "пусто"))

    return "\n".join(lines).strip() + "\n"


def is_bangkok_18(now: datetime | None = None) -> bool:
    local = (now or datetime.now(UTC)).astimezone(BANGKOK)
    return local.hour == 18


def send_telegram(text: str) -> bool:
    token = os.environ.get("ERROR_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("ERROR_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("нет ERROR_BOT_TOKEN / ERROR_CHAT_ID", file=sys.stderr)
        return False
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text[:4000],
            "disable_web_page_preview": "true",
        }
    ).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
            ),
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"telegram send failed: {exc}", file=sys.stderr)
        return False
    return True


def _checkbox(page: dict[str, Any], name: str) -> bool:
    prop = (page.get("properties") or {}).get(name) or {}
    return bool(prop.get("checkbox"))


def _rich_text(page: dict[str, Any], name: str) -> str:
    prop = (page.get("properties") or {}).get(name) or {}
    return "".join(x.get("plain_text", "") for x in (prop.get("rich_text") or []))


def _select_name(page: dict[str, Any], name: str) -> str:
    prop = (page.get("properties") or {}).get(name) or {}
    if prop.get("type") == "status":
        return str(((prop.get("status") or {}).get("name")) or "")
    return str(((prop.get("select") or {}).get("name")) or "")


def fetch_queues() -> tuple[list[str], list[str]] | None:
    token = os.environ.get("NOTION_API_KEY") or os.environ.get("NOTION_TOKEN")
    db = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("NOTION_DB_ID")
    if not token or not db:
        return None
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    payload = json.dumps(
        {
            "page_size": 100,
            "filter": {"property": "Статус", "status": {"equals": "ready_to_post"}},
        }
    ).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{db}/query",
        data=payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    groups: list[str] = []
    marketplace: list[str] = []
    for page in data.get("results") or []:
        if _select_name(page, "Публикация") != "ДА":
            continue
        oid = _rich_text(page, "Объект ID") or "?"
        if not _checkbox(page, "phone_fb_groups_done"):
            groups.append(oid)
        if not _checkbox(page, "phone_fb_marketplace_done"):
            marketplace.append(oid)
    return groups, marketplace


def build_report(day: date) -> str:
    start, end = day_bounds_utc(day)
    groups_posts = filter_posts(
        load_posts(pp.package_root() / "data" / "fb_groups" / "state.json"),
        start,
        end,
    )
    mp_posts = filter_posts(
        load_posts(pp.package_root() / "data" / "fb_marketplace" / "state.json"),
        start,
        end,
    )
    queue_g = queue_m = None
    try:
        queues = fetch_queues()
    except Exception as exc:  # noqa: BLE001
        print(f"[fb_daily_report] очередь Notion недоступна: {exc}", file=sys.stderr)
        queues = None
    if queues:
        queue_g, queue_m = queues
    return format_report(
        day,
        summarize_groups(groups_posts),
        summarize_marketplace(mp_posts),
        queue_groups=queue_g,
        queue_marketplace=queue_m,
    )


def main() -> int:
    pp.load_dotenv()
    parser = argparse.ArgumentParser(description="Ежедневный отчёт FB в бот ошибок")
    parser.add_argument("--send", action="store_true", help="Отправить в ERROR_CHAT_ID")
    parser.add_argument("--date", help="День Пхукета YYYY-MM-DD (иначе авто)")
    parser.add_argument(
        "--if-bangkok-18",
        action="store_true",
        help="Выйти без отправки, если сейчас не 18:00 в Asia/Bangkok",
    )
    args = parser.parse_args()

    if args.if_bangkok_18 and not is_bangkok_18():
        now = datetime.now(BANGKOK).strftime("%H:%M %Z")
        print(f"skip: сейчас {now}, отчёт только в 18:00 Бангкок")
        return 0

    override = date.fromisoformat(args.date) if args.date else None
    day = report_day(override=override)
    text = build_report(day)
    print(text, end="")
    if not args.send:
        return 0
    if send_telegram(text):
        print("sent")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
