#!/usr/bin/env python3
"""Agent 4 — ветка 2: публикация объектов из Notion CRM в группы Facebook.

Постит браузерной автоматизацией (Playwright, персистентный профиль с живой
авторизацией FB — тот же аккаунт, что у парсера agent_1_parser/fb_parser).
Metricool-ветку (publish_pipeline.py) не трогает: отдельный процесс, отдельные
lock-поля (fb_groups_locked / fb_groups_taken_at), статус «Статус» не меняет.

Текст поста — колонка «Описание для FB Marketplace» как есть (в конце тег
#A_YYYYMMDD_NNN). Фото — из галереи R2 (колонка «Фото»), первой идёт
хук-обложка 000_hook_cover.jpg. Результат — ссылка на пост в post_url_fb_groups
и журнал в fb_groups_log.

Запуск (нужен python3.11 с playwright — venv парсера):
  VENV=agent_1_parser/fb_parser/.venv311/bin/python
  $VENV fb_groups_pipeline.py --page-id PAGE_ID [--dry-run]
  $VENV fb_groups_pipeline.py --queue
  $VENV fb_groups_pipeline.py --page-id PAGE_ID --group https://www.facebook.com/groups/XXX/
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import publish_pipeline as pp  # чтение Notion, разбор галереи, схема, .env
import fb_account_guard as guard  # общий предохранитель FB-аккаунта
from fb_human import (  # noqa: E402
    STEALTH_INIT_SCRIPT,
    human_click,
    human_delay,
    idle_scroll,
    read_before_submit,
    reset_cursor,
    type_into,
    type_like_human,
    wander_mouse,
)

USER_AGENT_TAG = "real-estate-agent4-fb-groups/1.0"

# Плейсхолдер композера в шапке ленты группы (EN/RU/UK)
COMPOSER_RE = re.compile(
    r"write something|напишите что|напишіть|что у вас|що у вас|anything else|"
    r"поделитесь|поділіться|discuss",
    re.I,
)
# В группах FB RU сейчас CTA «Отправить», не «Опубликовать».
POST_BUTTON_RE = re.compile(
    r"^\s*(post|publish|share|опубликовать|опублікувати|отправить|надіслати|send)\s*$",
    re.I,
)
PHOTO_BUTTON_RE = re.compile(r"photo|фото|зображення|image", re.I)
JOIN_BUTTON_RE = re.compile(r"^(join group|вступить в группу|приєднатися до групи)$", re.I)
# Кнопка отправки в диалоге заявки на вступление (правила группы / вопросы)
JOIN_SUBMIT_RE = re.compile(
    r"надіслати|подати запит|submit|send request|отправить|приєднатися|join", re.I
)
PENDING_MARKERS = (
    "pending",
    "ожидает",
    "на рассмотрении",
    "очікує",
    "на розгляді",
    "will be visible once",
    "будет виден после",
    "буде видно",
)
POST_HREF_RE = re.compile(r"/(?:posts|permalink)/[\w:]+", re.I)


def load_fb_groups_config() -> dict[str, Any]:
    config_path = pp.package_root() / "config" / "fb_groups.json"
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Состояние лимитов частоты (локальный файл, не Notion)
# ---------------------------------------------------------------------------

def state_path(cfg: dict[str, Any]) -> Path:
    return pp.package_root() / cfg.get("state_file", "data/fb_groups/state.json")


def load_state(cfg: dict[str, Any]) -> dict[str, Any]:
    path = state_path(cfg)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"posts": []}


def save_state(cfg: dict[str, Any], state: dict[str, Any]) -> None:
    path = state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_post(cfg: dict[str, Any], group_url: str, object_id: str, post_url: str) -> None:
    state = load_state(cfg)
    state.setdefault("posts", []).append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "group": group_url,
            "object_id": object_id,
            "post_url": post_url,
        }
    )
    save_state(cfg, state)


def _post_ts(entry: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(entry["ts"])


def listed_minutes(raw: Any, default: list[float]) -> list[float]:
    if isinstance(raw, (int, float)):
        return [float(raw)]
    values = [float(x) for x in (raw or []) if isinstance(x, (int, float))]
    return values or list(default)


def min_minutes_between_objects(cfg: dict[str, Any]) -> float:
    limits = cfg.get("limits", {})
    raw = limits.get("minutes_between_objects")
    if raw is None:
        raw = limits.get("min_minutes_between_objects", 30)
    return min(listed_minutes(raw, [30.0]))


def same_object_same_group_minutes(cfg: dict[str, Any]) -> int:
    limits = cfg.get("limits", {})
    raw = limits.get("min_minutes_same_object_same_group", 30)
    return int(min(listed_minutes(raw, [30.0])))


def check_rate_limits(
    cfg: dict[str, Any],
    group_url: str,
    *,
    object_id: str | None = None,
    ignore_same_group: bool = False,
) -> str | None:
    """None — можно постить, иначе причина отказа.

    Пауза 2–5 мин между группами живёт в pause_between_groups, сюда не входит.
    Разные объекты могут идти в одну и ту же группу.
    """
    limits = cfg.get("limits", {})
    now = datetime.now(timezone.utc)
    posts = load_state(cfg).get("posts", [])

    day_ago = now - timedelta(days=1)
    last_day = [p for p in posts if _post_ts(p) > day_ago]
    budget = int(limits.get("daily_group_posts_budget", 50))
    if len(last_day) >= budget:
        return f"дневной лимит {budget} постов в группы исчерпан"
    max_day = int(limits.get("max_posts_per_day_total", 15))
    objects_today = {p.get("object_id") for p in last_day if p.get("object_id")}
    if object_id:
        if object_id not in objects_today and len(objects_today) >= max_day:
            return f"дневной лимит {max_day} объектов исчерпан"
    elif len(objects_today) >= max_day:
        return f"дневной лимит {max_day} объектов исчерпан"

    object_gap = min_minutes_between_objects(cfg)
    last = posts[-1] if posts else None
    if last and object_id and last.get("object_id") and last.get("object_id") != object_id:
        if _post_ts(last) > now - timedelta(minutes=object_gap):
            return (
                f"пауза между объектами {object_gap:.0f} мин не выдержана "
                f"(последний {last.get('object_id')} {last.get('ts')})"
            )

    if ignore_same_group:
        return None

    group_gap = same_object_same_group_minutes(cfg)
    group_key = canonical_group_url(group_url)
    same = [
        p
        for p in posts
        if canonical_group_url(p.get("group") or "") == group_key
        and (not object_id or p.get("object_id") == object_id)
        and _post_ts(p) > now - timedelta(minutes=group_gap)
    ]
    if same:
        return f"этот объект в эту группу уже постили за последние {group_gap} мин"
    return None


def should_stop_fb_queue(reason: str) -> bool:
    """Очередь останавливаем только если сейчас нельзя открывать браузер / новый объект.

    Пропуск одной группы или одного объекта («уже постили», locked, done)
    не должен рвать остальные.
    """
    text = (reason or "").lower()
    if "предохранитель" in text:
        return True
    if "дневной лимит" in text:
        return True
    if "пауза между объектами" in text or "между объектами" in text:
        return True
    return False


def posts_today_count(cfg: dict[str, Any]) -> int:
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    posts = load_state(cfg).get("posts") or []
    return sum(1 for p in posts if p.get("ts") and _post_ts(p) > day_ago)


def groups_per_object(
    cfg: dict[str, Any],
    *,
    waiting: int,
    remaining_budget: int,
    pool: int,
) -> int:
    """Сколько групп дать одному объекту из дневного бюджета."""
    if remaining_budget <= 0 or pool <= 0:
        return 0
    limits = cfg.get("limits", {})
    min_n = int(limits.get("min_groups_per_object", 4))
    max_n = int(limits.get("max_groups_per_object", 12))
    sparse_max = int(limits.get("sparse_queue_max_groups", 20))
    sparse_objects = int(limits.get("sparse_queue_objects", 2))
    waiting = max(1, int(waiting))
    effective_max = sparse_max if waiting <= sparse_objects else max_n
    effective_max = min(effective_max, pool, remaining_budget)
    n = remaining_budget // waiting
    n = min(max(n, 1), effective_max)
    if remaining_budget >= min_n * waiting:
        n = max(n, min_n)
    return min(n, effective_max, remaining_budget)


def take_round_robin(
    groups: list[dict[str, Any]],
    cursor: int,
    n: int,
    *,
    skip_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Следующие n групп по кругу. skip_keys — уже выданные этому объекту."""
    skip_keys = skip_keys or set()
    if not groups or n <= 0:
        return [], cursor
    out: list[dict[str, Any]] = []
    i = cursor % len(groups)
    scanned = 0
    while len(out) < n and scanned < len(groups):
        group = groups[i]
        i = (i + 1) % len(groups)
        scanned += 1
        key = canonical_group_url(group["url"])
        if not key or key in skip_keys:
            continue
        out.append(group)
    return out, i


def _assignment_urls(state: dict[str, Any], object_id: str) -> list[str]:
    if not object_id:
        return []
    row = (state.get("assignments") or {}).get(object_id) or {}
    return [u for u in (row.get("urls") or []) if u]


def resolve_groups_by_urls(
    urls: list[str], groups: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_key = {canonical_group_url(g["url"]): g for g in groups}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url in urls:
        key = canonical_group_url(url)
        group = by_key.get(key)
        if not key or key in seen or not group:
            continue
        seen.add(key)
        out.append(group)
    return out


def assignment_has_remaining(
    cfg: dict[str, Any], object_id: str, groups: list[dict[str, Any]]
) -> bool:
    posted = posted_group_keys_from_state(cfg, object_id)
    urls = _assignment_urls(load_state(cfg), object_id)
    if urls:
        assigned = resolve_groups_by_urls(urls, groups)
        return any(canonical_group_url(g["url"]) not in posted for g in assigned)
    return bool(posted)


def ensure_assignment(
    cfg: dict[str, Any],
    object_id: str,
    groups: list[dict[str, Any]],
    posted_keys: set[str],
    *,
    waiting: int,
    force: bool,
    persist: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Пачка групп объекта. new_cycle=True, если только что нарезали с курсора."""
    state = load_state(cfg)
    assignments: dict[str, Any] = dict(state.get("assignments") or {})
    cursor = int(state.get("group_cursor") or 0)
    budget = int(cfg.get("limits", {}).get("daily_group_posts_budget", 50))
    remaining_budget = max(0, budget - posts_today_count(cfg))
    n = groups_per_object(
        cfg, waiting=waiting, remaining_budget=remaining_budget, pool=len(groups)
    )

    existing_urls = _assignment_urls(state, object_id)
    if existing_urls and not force:
        assigned = resolve_groups_by_urls(existing_urls, groups)
        if assigned:
            return assigned, False

    if n <= 0:
        return [], False

    skip = set(posted_keys) if (posted_keys and not force) else set()
    seed = resolve_groups_by_urls(
        [g["url"] for g in groups if canonical_group_url(g["url"]) in skip],
        groups,
    )
    need = max(0, n - len(seed))
    extra, cursor = take_round_robin(groups, cursor, need, skip_keys=skip)
    assigned = seed + extra
    if not assigned:
        return [], False

    new_cycle = not bool(existing_urls and not force)
    if persist and object_id:
        assignments[object_id] = {
            "urls": [g["url"] for g in assigned],
            "created_ts": datetime.now(timezone.utc).isoformat(),
        }
        state["assignments"] = assignments
        state["group_cursor"] = cursor
        save_state(cfg, state)
        print(
            f"[fb_groups] {object_id}: пачка {len(assigned)} групп "
            f"(бюджет остаток {remaining_budget}, в очереди {waiting}, курсор {cursor})",
            flush=True,
        )
    return assigned, new_cycle


def sort_queue_resume_first(
    pages: list[dict[str, Any]],
    cfg: dict[str, Any],
    groups: list[dict[str, Any]],
    fields: dict[str, str],
) -> list[dict[str, Any]]:
    """Сначала объекты с незакрытой пачкой — добить хвост, не начинать чужой."""
    partial: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    for page in pages:
        oid = pp.get_prop(page, fields["object_id"], "rich_text") or ""
        if assignment_has_remaining(cfg, oid, groups):
            partial.append(page)
        else:
            rest.append(page)
    return partial + rest


# ---------------------------------------------------------------------------
# Фото: галерея R2 -> локальные файлы (FB грузит только локальные)
# ---------------------------------------------------------------------------

def resolve_image_urls(gallery_url: str, cfg: dict[str, Any]) -> list[str]:
    media = cfg.get("media", {})
    max_images = int(media.get("max_images", 10))
    urls = pp.parse_gallery_image_urls(gallery_url)
    if media.get("hook_cover_first", True):
        cover = pp.hook_cover_url(gallery_url)
        if cover:
            urls = [cover] + [u for u in urls if u != cover]
    return urls[:max_images]


def download_images(urls: list[str], object_id: str, cfg: dict[str, Any]) -> list[Path]:
    tmp_root = pp.package_root() / cfg.get("tmp_dir", "data/fb_groups/tmp") / object_id
    tmp_root.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, url in enumerate(urls):
        ext = ".jpg"
        m = re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, re.I)
        if m:
            ext = "." + m.group(1).lower().replace("jpeg", "jpg")
        target = tmp_root / f"{i:03d}{ext}"
        if target.exists() and target.stat().st_size > 5_000:
            paths.append(target)
            continue
        request = urllib.request.Request(url, method="GET")
        request.add_header("User-Agent", USER_AGENT_TAG)
        with urllib.request.urlopen(request, timeout=60) as resp:
            target.write_bytes(resp.read())
        paths.append(target)
    return paths


# ---------------------------------------------------------------------------
# Браузер
# ---------------------------------------------------------------------------

def pick_pause_minutes(cfg: dict[str, Any], key: str = "minutes_between_groups") -> float:
    """Случайная пауза из списка в конфиге (не диапазон min–max)."""
    defaults = {
        "minutes_between_groups": [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
        "minutes_between_objects": [30.0, 31.0, 32.0, 33.0, 34.0, 35.0],
    }
    fallback = defaults.get(key, defaults["minutes_between_groups"])
    raw = cfg.get("limits", {}).get(key, fallback)
    return random.choice(listed_minutes(raw, fallback))


def pause_between_groups(cfg: dict[str, Any]) -> None:
    """Пауза между группами: список из конфига, каждый раз заново с диска."""
    try:
        disk = load_fb_groups_config()
        limits = {**cfg.get("limits", {}), **disk.get("limits", {})}
        cfg = {**cfg, "limits": limits}
    except Exception:
        pass
    minutes = pick_pause_minutes(cfg)
    print(f"[fb_groups] пауза перед следующей группой: {minutes:.1f} мин")
    time.sleep(minutes * 60)


def profile_path(cfg: dict[str, Any]) -> Path:
    browser = cfg.get("browser", {})
    env_name = browser.get("profile_env", "FB_GROUPS_BROWSER_PROFILE")
    raw = os.environ.get(env_name, "").strip() or browser.get("default_profile", ".fb_profile")
    path = Path(raw)
    if not path.is_absolute():
        path = pp.package_root() / path
    return path.resolve()


def resolve_headless() -> bool:
    return os.environ.get("FB_HEADLESS", "false").strip().lower() == "true"


class ProfileBusy(RuntimeError):
    pass


def acquire_profile_lock(profile: Path) -> Path:
    """Профиль нельзя открывать двумя процессами (Chromium сломает сессию)."""
    lock_file = profile / ".profile.lock"
    if lock_file.exists():
        age = time.time() - lock_file.stat().st_mtime
        if age < 30 * 60:
            raise ProfileBusy(
                f"Профиль занят другим процессом ({lock_file}, возраст {age:.0f}с). "
                "Дождись завершения или удали lock-файл."
            )
        lock_file.unlink(missing_ok=True)
    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    return lock_file


def resolve_proxy(cfg: dict[str, Any] | None = None) -> dict[str, str] | None:
    """Прокси постинг-аккаунта этой ветки (config.account.proxy_env).
    Свой, не парсинговый и не от другой ветки.
    Формат: scheme://user:pass@host:port или host:port:user:pass."""
    raw = account_env(cfg or {}, "proxy_env")
    if not raw:
        return None
    if "://" in raw:
        from urllib.parse import urlparse
        u = urlparse(raw)
        proxy: dict[str, str] = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
        if u.username:
            proxy["username"] = u.username
        if u.password:
            proxy["password"] = u.password
        return proxy
    parts = raw.split(":")
    if len(parts) == 2:
        return {"server": f"http://{parts[0]}:{parts[1]}"}
    if len(parts) == 4:
        host, port, user, pw = parts
        return {"server": f"http://{host}:{port}", "username": user, "password": pw}
    print(f"[warn] прокси постинг-аккаунта нераспознан: {raw}", file=sys.stderr)
    return None


def open_browser(p: Any, cfg: dict[str, Any]) -> Any:
    browser = cfg.get("browser", {})
    viewport = browser.get("viewport", {"width": 1440, "height": 900})
    launch_kwargs: dict[str, Any] = {
        "headless": resolve_headless(),
        "viewport": {"width": int(viewport["width"]), "height": int(viewport["height"])},
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        "ignore_default_args": ["--enable-automation"],
        "timezone_id": "Asia/Bangkok",
        "locale": "en-US",
    }
    proxy = resolve_proxy(cfg)
    if proxy:
        launch_kwargs["proxy"] = proxy
    context = p.chromium.launch_persistent_context(str(profile_path(cfg)), **launch_kwargs)
    context.add_init_script(STEALTH_INIT_SCRIPT)
    context.set_default_timeout(int(browser.get("action_timeout_ms", 30000)))
    reset_cursor()
    return context


def is_logged_in(context: Any) -> bool:
    return any(
        c.get("name") == "c_user" and "facebook.com" in c.get("domain", "")
        for c in context.cookies()
    )


def _facebook_auth_challenge(url: str) -> bool:
    u = (url or "").lower()
    return any(
        token in u
        for token in (
            "checkpoint",
            "two_step_verification",
            "auth_platform",
            "login/identify",
        )
    )


def account_env(cfg: dict[str, Any], key: str) -> str:
    """Имя env-переменной для аккаунта этой ветки берём из конфига
    (account.email_env / password_env / proxy_env). У каждой ветки
    (группы / Marketplace) СВОЙ аккаунт — общего fallback нет, не смешиваем."""
    var = (cfg.get("account", {}) or {}).get(key, "")
    return os.environ.get(var, "").strip() if var else ""


def try_relogin(page: Any, cfg: dict[str, Any]) -> None:
    """Запасной вариант: разовый логин ПОСТИНГ-аккаунта этой ветки
    (не парсингового!). Аккаунт задаётся в config.account.*_env."""
    email = account_env(cfg, "email_env")
    password = account_env(cfg, "password_env")
    if not email or not password:
        raise RuntimeError(
            "AUTH_REQUIRED: сессия протухла, а логин/пароль постинг-аккаунта этой "
            "ветки не заданы (см. config.account.*_env; аккаунт отдельный "
            "от парсингового FB_EMAIL и от другой ветки постинга)"
        )
    page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
    human_delay(cfg)
    if _facebook_auth_challenge(page.url):
        raise RuntimeError(
            "AUTH_REQUIRED: Facebook checkpoint/2FA — заверши логин вручную "
            "(FB_HEADLESS=false) и перезапусти."
        )
    page.fill('input[name="email"], input#email', email)
    human_delay(cfg)
    page.fill('input[name="pass"], input#pass', password)
    human_delay(cfg)
    login_btn = page.get_by_role("button", name=re.compile(r"log in|войти|เข้าสู่ระบบ", re.I))
    if login_btn.count() and login_btn.first.is_visible():
        human_click(page, login_btn.first, cfg)
    else:
        page.locator('input[name="pass"], input#pass').press("Enter")
    page.wait_for_load_state("domcontentloaded", timeout=90000)
    human_delay(cfg, 2.0)
    if _facebook_auth_challenge(page.url):
        raise RuntimeError(
            "AUTH_REQUIRED: Facebook checkpoint/2FA — заверши логин вручную "
            "(FB_HEADLESS=false) и перезапусти."
        )


# ---------------------------------------------------------------------------
# Постинг в группу
# ---------------------------------------------------------------------------

def collect_post_hrefs(page: Any) -> set[str]:
    hrefs: set[str] = set()
    for a in page.locator('a[href*="/posts/"], a[href*="/permalink/"]').all():
        href = a.get_attribute("href") or ""
        m = POST_HREF_RE.search(href)
        if m:
            hrefs.add(canonical_post_url(href))
    return hrefs


def canonical_post_url(href: str) -> str:
    href = href.split("?", 1)[0]
    if href.startswith("/"):
        href = "https://www.facebook.com" + href
    return href.rstrip("/") + "/"


def handle_join_request_dialog(page: Any, cfg: dict[str, Any]) -> bool:
    """Диалог «Запросы на участие»: принять правила (чекбокс) и отправить заявку.

    Появляется у групп с правилами/вопросами. Чекбоксы отмечаем все,
    текстовые вопросы не заполняем (необязательные), жмём «Отправить».
    """
    dialogs = page.locator('div[role="dialog"]')
    if not dialogs.count():
        return False
    dialog = dialogs.last

    # «Я принимаю правила группы» и подобные согласия
    boxes = dialog.locator('[role="checkbox"][aria-checked="false"], input[type="checkbox"]')
    for i in range(boxes.count()):
        try:
            human_click(page, boxes.nth(i), cfg)
            human_delay(cfg, 0.5)
        except Exception:
            continue

    submit = dialog.get_by_role("button", name=JOIN_SUBMIT_RE)
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if submit.count():
            btn = submit.first
            if btn.get_attribute("aria-disabled") not in ("true", "1"):
                human_click(page, btn, cfg)
                human_delay(cfg, 2.0)
                return True
        time.sleep(1.0)
    debug_screenshot(page, "join_dialog_stuck")
    return False


CLOSE_BUTTON_RE = re.compile(r"^(закрити|close|закрыть)$", re.I)


def close_blocking_dialogs(page: Any, cfg: dict[str, Any]) -> None:
    """Закрыть оверлеи (приветствие группы, подсказки), перекрывающие ленту.

    Композер не трогаем: закрываем только диалоги без contenteditable-textbox.
    """
    for _ in range(4):
        dialogs = page.locator('div[role="dialog"]')
        blocking = None
        for i in range(dialogs.count()):
            d = dialogs.nth(i)
            try:
                if not d.is_visible():
                    continue
                if d.locator('div[role="textbox"][contenteditable="true"]').count():
                    continue
                blocking = d
                break
            except Exception:
                continue
        if blocking is None:
            return
        try:
            close_btn = blocking.get_by_role("button", name=CLOSE_BUTTON_RE)
            if close_btn.count():
                human_click(page, close_btn.first, cfg)
            else:
                page.keyboard.press("Escape")
        except Exception:
            page.keyboard.press("Escape")
        human_delay(cfg)


def maybe_join_group(page: Any, cfg: dict[str, Any]) -> bool:
    """Если аккаунт не в группе — жмём «Вступить». Возвращает True, если жали."""
    join = page.locator('[role="main"]').get_by_role("button", name=JOIN_BUTTON_RE)
    if not join.count():
        return False
    human_click(page, join.first, cfg)
    human_delay(cfg, 3.0)
    # Группа может показать диалог с правилами/вопросами — принимаем и отправляем
    handle_join_request_dialog(page, cfg)
    page.reload(wait_until="domcontentloaded")
    human_delay(cfg, 3.0)
    return True


def debug_screenshot(page: Any, name: str) -> str:
    out_dir = pp.package_root() / "data" / "fb_groups" / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}_{datetime.now(timezone.utc).strftime('%H%M%S')}.png"
    try:
        page.screenshot(path=str(path))
    except Exception:
        return ""
    return str(path)


def open_composer(page: Any, cfg: dict[str, Any]) -> Any:
    timeout = int(cfg.get("browser", {}).get("action_timeout_ms", 30000))
    composer = page.locator('[role="main"] [role="button"]', has_text=COMPOSER_RE)
    if not composer.count():
        # Фолбэк: возможно, постить нельзя без членства — пробуем вступить
        if maybe_join_group(page, cfg):
            composer = page.locator('[role="main"] [role="button"]', has_text=COMPOSER_RE)
        if not composer.count():
            raise RuntimeError(
                "COMPOSER_NOT_FOUND: не нашёл поле «Напишите что-нибудь…». "
                "Скорее всего заявка на вступление в группу ещё не одобрена. "
                "Скриншот: " + debug_screenshot(page, "composer_not_found")
            )
    human_click(page, composer.first, cfg)
    # Диалогов с role=dialog на странице несколько — нужен тот, где есть textbox
    dialog = page.locator(
        'div[role="dialog"]',
        has=page.locator('div[role="textbox"][contenteditable="true"]'),
    ).last
    try:
        dialog.locator('div[role="textbox"][contenteditable="true"]').first.wait_for(
            state="visible", timeout=timeout
        )
    except Exception as e:
        shot = debug_screenshot(page, "composer_no_textbox")
        raise RuntimeError(
            f"COMPOSER_NO_TEXTBOX: форма поста не открылась (вероятно, нужно членство "
            f"в группе или FB показал промежуточный диалог). Скриншот: {shot}"
        ) from e
    return dialog


def attach_photos(page: Any, dialog: Any, files: list[Path], cfg: dict[str, Any]) -> None:
    if not files:
        return
    file_input = dialog.locator('input[type="file"][accept*="image"]')
    if not file_input.count():
        file_input = dialog.locator('input[type="file"]')
    if not file_input.count():
        photo_btn = dialog.get_by_role("button", name=PHOTO_BUTTON_RE)
        if photo_btn.count():
            human_click(page, photo_btn.first, cfg)
            human_delay(cfg)
            file_input = dialog.locator('input[type="file"]')
    if not file_input.count():
        raise RuntimeError("PHOTO_INPUT_NOT_FOUND: не нашёл input для загрузки фото в композере")
    file_input.first.set_input_files([str(f) for f in files])

    # FB показывает в коллаже только первые ~5 превью («+N» поверх последнего),
    # поэтому ждём не точное число, а: есть превью, нет прогресс-баров,
    # и количество превью стабильно несколько секунд подряд.
    deadline = time.time() + int(cfg.get("browser", {}).get("upload_wait_ms", 60000)) / 1000.0
    min_previews = min(len(files), 5)
    last_count = -1
    stable_since: float | None = None
    previews = 0
    while time.time() < deadline:
        previews = dialog.locator('img[src^="blob:"], img[src*="scontent"]').count()
        uploading = dialog.locator('[role="progressbar"]').count()
        if previews >= min_previews and uploading == 0:
            if previews != last_count:
                stable_since = time.time()
            elif stable_since and time.time() - stable_since >= 4.0:
                return
        else:
            stable_since = None
        last_count = previews
        time.sleep(1.0)
    raise RuntimeError(
        f"UPLOAD_TIMEOUT: превью {previews}, ожидалось минимум {min_previews} без прогресс-баров"
    )


def button_label(el: Any) -> str:
    """Accessible name / inner text первой строки — то, что видит Playwright name=."""
    try:
        raw = (el.get_attribute("aria-label") or el.inner_text() or "").strip()
    except Exception:
        return ""
    return raw.split("\n", 1)[0].strip()


def is_composer_submit_label(text: str) -> bool:
    return bool(POST_BUTTON_RE.match((text or "").strip()))


def control_is_enabled(el: Any) -> bool:
    try:
        if not el.is_visible():
            return False
        aria = (el.get_attribute("aria-disabled") or "").strip().lower()
        if aria in ("true", "1"):
            return False
        disabled = el.get_attribute("disabled")
        if disabled is not None and disabled != "false":
            return False
        return True
    except Exception:
        return False


def composer_submit_button(dialog: Any) -> Any | None:
    """Кнопка публикации в композере: Post / Опубликовать / Отправить / Send."""
    named = dialog.get_by_role("button", name=POST_BUTTON_RE)
    for i in range(named.count()):
        btn = named.nth(i)
        if control_is_enabled(btn):
            return btn
    buttons = dialog.get_by_role("button")
    for i in range(buttons.count()):
        btn = buttons.nth(i)
        if is_composer_submit_label(button_label(btn)) and control_is_enabled(btn):
            return btn
    if named.count():
        return named.first
    return None


def click_post(page: Any, dialog: Any, cfg: dict[str, Any]) -> None:
    deadline = time.time() + int(cfg.get("browser", {}).get("upload_wait_ms", 60000)) / 1000.0
    while time.time() < deadline:
        btn = composer_submit_button(dialog)
        if btn is not None and control_is_enabled(btn):
            read_before_submit(page, cfg)
            human_click(page, btn, cfg)
            return
        time.sleep(1.0)
    raise RuntimeError(
        "POST_BUTTON_DISABLED: кнопка «Опубликовать»/«Отправить» не активировалась. "
        "Скриншот: " + debug_screenshot(page, "post_button_disabled")
    )


def wait_post_result(
    page: Any, group_url: str, before: set[str], cfg: dict[str, Any]
) -> dict[str, Any]:
    """Ждём закрытия композера и появления нового поста в ленте."""
    wait_ms = int(cfg.get("browser", {}).get("post_confirm_wait_ms", 45000))
    deadline = time.time() + wait_ms / 1000.0

    # 1. композер должен закрыться
    textbox = page.locator('div[role="dialog"] div[role="textbox"][contenteditable="true"]')
    while time.time() < deadline and textbox.count():
        time.sleep(1.0)

    # 2. ищем новый пост / маркер премодерации
    while time.time() < deadline:
        body_text = ""
        try:
            new_hrefs = collect_post_hrefs(page) - before
            group_posts = [h for h in new_hrefs if "/groups/" in h]
            if group_posts:
                return {"post_url": sorted(group_posts)[0], "pending_approval": False}
            body_text = (page.locator("body").inner_text(timeout=5000) or "").lower()
        except Exception:
            pass
        if any(m in body_text for m in PENDING_MARKERS):
            return {"post_url": None, "pending_approval": True}
        time.sleep(2.0)

    # Пост мог уйти в премодерацию: баннер «Ожидает одобрения администратора»
    # появляется только после перезагрузки страницы группы.
    try:
        page.reload(wait_until="domcontentloaded")
        time.sleep(4.0)
        body_text = (page.locator("body").inner_text(timeout=10000) or "").lower()
        if any(m in body_text for m in PENDING_MARKERS):
            return {"post_url": None, "pending_approval": True}
        new_hrefs = collect_post_hrefs(page) - before
        group_posts = [h for h in new_hrefs if "/groups/" in h]
        if group_posts:
            return {"post_url": sorted(group_posts)[0], "pending_approval": False}
    except Exception:
        pass
    return {"post_url": None, "pending_approval": False}


def post_to_group(
    page: Any, group_url: str, caption: str, files: list[Path], cfg: dict[str, Any]
) -> dict[str, Any]:
    nav_timeout = int(cfg.get("browser", {}).get("nav_timeout_ms", 90000))
    page.goto(group_url, wait_until="domcontentloaded", timeout=nav_timeout)
    human_delay(cfg, 2.0)
    wander_mouse(page)
    # Полистать ленту группы перед постингом — как живой участник
    idle_scroll(page, cfg, (10, 30))
    page.keyboard.press("Home")
    human_delay(cfg)

    # Если аккаунт не в группе — подписываемся до постинга
    if maybe_join_group(page, cfg):
        still_join = page.locator('[role="main"]').get_by_role("button", name=JOIN_BUTTON_RE)
        if still_join.count():
            raise RuntimeError(
                "JOIN_PENDING: отправлена заявка на вступление, членство ещё не одобрено "
                "— эту группу пропускаем. Скриншот: " + debug_screenshot(page, "join_pending")
            )

    # Приветственные оверлеи новой группы перекрывают композер
    close_blocking_dialogs(page, cfg)

    before = collect_post_hrefs(page)
    dialog = open_composer(page, cfg)
    human_delay(cfg)

    textbox = dialog.locator('div[role="textbox"][contenteditable="true"]').first
    human_click(page, textbox, cfg)
    human_delay(cfg)
    type_like_human(page, caption, cfg)
    human_delay(cfg)

    attach_photos(page, dialog, files, cfg)
    human_delay(cfg, 1.5)

    click_post(page, dialog, cfg)
    return wait_post_result(page, group_url, before, cfg)


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------

def append_fb_groups_log(page: dict[str, Any], fields: dict[str, str], line: str) -> dict[str, Any]:
    log_field = fields["fb_groups_log"]
    old = pp.get_prop(page, log_field, "rich_text") or ""
    combined = (old + "\n" + line).strip()
    if len(combined) > 1900:  # лимит rich_text-блока Notion — 2000 символов
        combined = combined[-1900:]
    return {log_field: {"rich_text": [{"text": {"content": combined}}]}}


def append_post_urls(
    page: dict[str, Any], fields: dict[str, str], new_urls: list[str]
) -> dict[str, Any]:
    """post_url_fb_groups (rich_text): все ссылки на посты, по одной на строку."""
    field = fields["post_url_fb_groups"]
    old = pp.get_prop(page, field, "rich_text") or ""
    urls = [u.strip() for u in old.splitlines() if u.strip()]
    for u in new_urls:
        if u and u not in urls:
            urls.append(u)
    combined = "\n".join(urls)[-1900:]
    return {field: {"rich_text": [{"text": {"content": combined}}]}}


def mark_failure(page_id: str, page: dict[str, Any], fields: dict[str, str], error: str) -> None:
    err_count = pp.get_prop(page, fields["error_count"], "number") or 0
    pp.notion_update_fields(
        page_id,
        {
            fields["publish_error"]: {"rich_text": [{"text": {"content": error[:2000]}}]},
            fields["error_count"]: {"number": err_count + 1},
        },
    )


# ---------------------------------------------------------------------------
# Пайплайн
# ---------------------------------------------------------------------------

def load_groups_file(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Текстовый список групп: одна строка — один URL, # — комментарий."""
    rel = cfg.get("groups_file")
    if not rel:
        return []
    path = pp.package_root() / rel
    if not path.exists():
        return []
    groups: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url = line.split()[0]
        if "facebook.com/groups/" not in url:
            print(f"[warn] строка в {rel} не похожа на URL группы: {line}", file=sys.stderr)
            continue
        groups.append({"name": url, "url": url, "enabled": True})
    return groups


def canonical_group_url(url: str) -> str:
    return (url or "").split("?")[0].rstrip("/")


def posted_group_keys_from_state(cfg: dict[str, Any], object_id: str) -> set[str]:
    """Группы, уже записанные в локальный state (на случай рестарта до записи в Notion)."""
    if not object_id:
        return set()
    keys: set[str] = set()
    for row in load_state(cfg).get("posts") or []:
        if row.get("object_id") != object_id:
            continue
        key = canonical_group_url(row.get("group") or "")
        if key:
            keys.add(key)
    return keys


def posted_group_keys_from_log(log_text: str, group_urls: list[str]) -> set[str]:
    blob = log_text or ""
    keys: set[str] = set()
    for url in group_urls:
        key = canonical_group_url(url)
        if key and key in blob:
            keys.add(key)
    return keys


def groups_for_cycle(
    groups: list[dict[str, Any]],
    posted_keys: set[str],
    *,
    done: bool,
    force: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Какие группы постить в этом запуске и это новый цикл (галочку сняли / --force)."""
    if done and not force:
        return [], False
    keys = [canonical_group_url(g["url"]) for g in groups]
    all_posted = bool(keys) and all(k in posted_keys for k in keys)
    new_cycle = bool(force or all_posted)
    if new_cycle:
        return list(groups), True
    remaining = [g for g in groups if canonical_group_url(g["url"]) not in posted_keys]
    return remaining, False


def cycle_is_complete(groups: list[dict[str, Any]], posted_keys: set[str]) -> bool:
    keys = [canonical_group_url(g["url"]) for g in groups]
    return bool(keys) and all(k in posted_keys for k in keys)


_FATAL_GROUP_MARKERS = ("AUTH_REQUIRED",)


def is_fatal_group_error(exc: BaseException) -> bool:
    msg = str(exc)
    return any(marker in msg for marker in _FATAL_GROUP_MARKERS)


def continue_after_group_error(cfg: dict[str, Any], exc: BaseException) -> bool:
    """True — эту группу пропускаем и постим следующую."""
    if is_fatal_group_error(exc):
        return False
    return bool(cfg.get("continue_on_group_error", True))


def recover_after_group_error(page: Any, cfg: dict[str, Any]) -> None:
    """Сбросить зависший композер/диалог, чтобы следующая группа открылась чистой."""
    for _ in range(3):
        try:
            page.keyboard.press("Escape")
        except Exception:
            break
    try:
        close_blocking_dialogs(page, cfg)
    except Exception:
        pass


def all_enabled_groups(cfg: dict[str, Any], override_url: str | None) -> list[dict[str, Any]]:
    if override_url:
        return [{"name": override_url, "url": override_url, "enabled": True}]
    groups = [g for g in cfg.get("groups", []) if g.get("enabled", True)]
    groups += load_groups_file(cfg)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for g in groups:
        key = canonical_group_url(g["url"])
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(g)
    return unique


def target_groups(cfg: dict[str, Any], override_url: str | None) -> list[dict[str, Any]]:
    groups = all_enabled_groups(cfg, override_url)
    max_groups = int(cfg.get("limits", {}).get("max_groups_per_run", 3))
    return groups[:max_groups]


def publish_object(
    page_id: str,
    cfg: dict[str, Any],
    *,
    groups: list[dict[str, Any]],
    dry_run: bool,
    force: bool,
    waiting_objects: int = 1,
) -> dict[str, Any]:
    fields = cfg["notion"]["fields"]
    status_ready = cfg["notion"]["statuses"]["ready"]

    page = pp.notion_get_page(page_id)
    object_id = pp.get_prop(page, fields["object_id"], "rich_text") or ""
    status = pp.get_prop(page, fields["status"], "status")
    caption = pp.get_prop(page, fields["caption"], "rich_text") or ""
    gallery_url = pp.get_prop(page, fields["photo"], "url") or ""
    locked = bool(pp.get_prop(page, fields["fb_groups_locked"], "checkbox"))
    done_field = fields.get("fb_groups_done") or "phone_fb_groups_done"
    already_done = bool(pp.get_prop(page, done_field, "checkbox"))
    log_text = (pp.get_prop(page, fields["fb_groups_log"], "rich_text") or "") + "\n" + (
        pp.get_prop(page, fields["post_url_fb_groups"], "rich_text") or ""
    )
    posted_keys = posted_group_keys_from_log(log_text, [g["url"] for g in groups])
    posted_keys |= posted_group_keys_from_state(cfg, object_id)

    result: dict[str, Any] = {
        "page_id": page_id,
        "object_id": object_id,
        "status": status,
        "groups": [],
        "caption_len": len(caption),
        "caption_preview": caption[:120],
        "fb_groups_done": already_done,
        "new_cycle": False,
    }

    if already_done and not force:
        return {**result, "skipped": True, "reason": "phone_fb_groups_done"}
    if locked and not force:
        return {**result, "skipped": True, "reason": "fb_groups_locked"}
    if status != status_ready and not force:
        return {**result, "skipped": True, "reason": f"status={status}, ожидался {status_ready}"}

    assigned, new_cycle = ensure_assignment(
        cfg,
        object_id,
        groups,
        posted_keys,
        waiting=waiting_objects,
        force=force,
        persist=not dry_run,
    )
    cycle_groups, _ = groups_for_cycle(
        assigned, posted_keys, done=False, force=force
    )
    max_groups = int(cfg.get("limits", {}).get("max_groups_per_run", 20))
    cycle_groups = cycle_groups[:max_groups]
    if cycle_groups and not force:
        random.shuffle(cycle_groups)
    result["new_cycle"] = new_cycle
    result["groups"] = [g["url"] for g in cycle_groups]
    result["assigned_groups"] = [g["url"] for g in assigned]

    if not cycle_groups:
        if assigned and cycle_is_complete(assigned, posted_keys):
            if not dry_run:
                pp.notion_update_fields(
                    page_id, {done_field: pp.notion_checkbox_property(True)}
                )
            return {
                **result,
                "skipped": True,
                "reason": "пачка групп уже закрыта",
                "fb_groups_done": True,
            }
        return {**result, "skipped": True, "reason": "нет групп для этого цикла"}
    if not caption.strip():
        raise ValueError(f"Пустая колонка «{fields['caption']}» у {object_id or page_id}")
    if object_id and f"#{object_id}" not in caption:
        print(f"[warn] в тексте нет тега #{object_id} — постим текст как есть", file=sys.stderr)
    if not gallery_url:
        raise ValueError(f"Пустая колонка «{fields['photo']}» у {object_id or page_id}")

    image_urls = resolve_image_urls(gallery_url, cfg)
    if not image_urls:
        raise ValueError(f"В галерее {gallery_url} не найдено изображений")
    result["images"] = len(image_urls)
    result["image_urls_preview"] = image_urls[:3]

    if not force:
        guard_reason = guard.check_account_guard()
        if guard_reason:
            return {**result, "skipped": True, "reason": f"предохранитель: {guard_reason}"}
        reason = check_rate_limits(
            cfg,
            cycle_groups[0]["url"],
            object_id=object_id or None,
            ignore_same_group=True,
        )
        if reason:
            return {**result, "skipped": True, "reason": f"лимит: {reason}"}
        keep: list[dict[str, Any]] = []
        for group in cycle_groups:
            group_reason = check_rate_limits(
                cfg,
                group["url"],
                object_id=object_id or None,
                ignore_same_group=False,
            )
            if group_reason and "этот объект в эту группу" in group_reason:
                print(
                    f"[fb_groups] пропускаем группу {group['url']}: {group_reason}",
                    file=sys.stderr,
                )
                continue
            if group_reason:
                return {**result, "skipped": True, "reason": f"лимит: {group_reason}"}
            keep.append(group)
        cycle_groups = keep
        result["groups"] = [g["url"] for g in cycle_groups]
        if not cycle_groups:
            return {
                **result,
                "skipped": True,
                "reason": "все оставшиеся группы на паузе 30 мин для этого объекта",
            }

    if dry_run:
        return {**result, "dry_run": True}

    files = download_images(image_urls, object_id or page_id, cfg)
    result["downloaded"] = [str(f) for f in files]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright не установлен в этом python. Запускай через venv парсера: "
            "agent_1_parser/fb_parser/.venv311/bin/python"
        ) from e

    # Лок ставим до открытия браузера — от параллельного двойного постинга
    pp.notion_update_fields(
        page_id,
        {
            fields["fb_groups_locked"]: pp.notion_checkbox_property(True),
            fields["fb_groups_taken_at"]: pp.notion_date_property(pp.utc_today_iso()),
        },
    )

    posted: list[dict[str, Any]] = []
    submitted = False
    lock_file = None
    try:
        profile = profile_path(cfg)
        if not profile.exists():
            raise RuntimeError(f"Профиль браузера не найден: {profile}")
        lock_file = acquire_profile_lock(profile)

        with sync_playwright() as p:
            context = open_browser(p, cfg)
            try:
                bpage = context.pages[0] if context.pages else context.new_page()
                bpage.goto(
                    "https://www.facebook.com/",
                    wait_until="domcontentloaded",
                    timeout=int(cfg.get("browser", {}).get("nav_timeout_ms", 90000)),
                )
                human_delay(cfg)
                if not is_logged_in(context):
                    try_relogin(bpage, cfg)
                    if not is_logged_in(context):
                        raise RuntimeError("AUTH_REQUIRED: логин не удался")

                # «Пришёл человек»: сначала полистать главную ленту
                idle_scroll(bpage, cfg, (15, 45))

                # Ошибка в одной группе не валит весь список (config.continue_on_group_error).
                for i, group in enumerate(cycle_groups):
                    if i > 0:
                        pause_between_groups(cfg)
                    try:
                        out = post_to_group(bpage, group["url"], caption, files, cfg)
                        submitted = True
                        out["group_url"] = group["url"]
                        posted.append(out)
                        record_post(cfg, group["url"], object_id, out.get("post_url") or "")
                    except Exception as e:
                        posted.append({"group_url": group["url"], "error": str(e)})
                        debug_screenshot(bpage, "group_error")
                        recover_after_group_error(bpage, cfg)
                        if not continue_after_group_error(cfg, e):
                            print(
                                f"[fb_groups] стоп: {group['url']}: {e}",
                                file=sys.stderr,
                            )
                            raise
                        print(
                            f"[fb_groups] группа пропущена, идём дальше: {group['url']}: {e}",
                            file=sys.stderr,
                        )
                # Не закрывать браузер сразу после «Опубликовать»
                idle_scroll(bpage, cfg, (10, 25))
            finally:
                context.close()
                guard.record_session_end(branch="fb_groups")
        if not submitted:
            errors = "; ".join(f"{p['group_url']}: {p.get('error')}" for p in posted)
            raise RuntimeError(f"ни в одну группу не запостилось — {errors}")
    except Exception as e:
        mark_failure(page_id, page, fields, f"fb_groups: {e}")
        raise
    finally:
        try:
            pp.notion_update_fields(
                page_id, {fields["fb_groups_locked"]: pp.notion_checkbox_property(False)}
            )
        except Exception:
            pass
        if lock_file:
            lock_file.unlink(missing_ok=True)

    result["posted"] = posted

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    log_lines = []
    saved_urls: list[str] = []
    success_keys: set[str] = set()
    for out in posted:
        if out.get("post_url"):
            saved_urls.append(out["post_url"])
            log_lines.append(f"{now} {out['group_url']} -> {out['post_url']}")
            success_keys.add(canonical_group_url(out["group_url"]))
        elif out.get("pending_approval"):
            log_lines.append(f"{now} {out['group_url']} -> pending_approval")
            success_keys.add(canonical_group_url(out["group_url"]))
        elif out.get("error"):
            log_lines.append(f"{now} {out['group_url']} -> ERROR: {out['error'][:120]}")
        else:
            log_lines.append(f"{now} {out['group_url']} -> опубликовано, url не найден")
            success_keys.add(canonical_group_url(out["group_url"]))

    posted_after = success_keys if force else (posted_keys | success_keys)
    done_now = cycle_is_complete(assigned, posted_after)

    props: dict[str, Any] = {
        fields["publish_error"]: {"rich_text": []},
        done_field: pp.notion_checkbox_property(done_now),
        **append_fb_groups_log(page, fields, "\n".join(log_lines)),
    }
    if saved_urls:
        props.update(append_post_urls(page, fields, saved_urls))
    pp.notion_update_fields(page_id, props)
    result["saved_urls"] = saved_urls
    result["fb_groups_done"] = done_now
    return result


def main() -> int:
    pp.load_dotenv()
    cfg = load_fb_groups_config()

    parser = argparse.ArgumentParser(description="Notion CRM → группы Facebook (Playwright)")
    parser.add_argument("--page-id", help="Notion page ID объекта")
    parser.add_argument("--queue", action="store_true", help="Все объекты в ready_to_post")
    parser.add_argument("--group", help="URL группы (перекрывает config/fb_groups.json)")
    parser.add_argument("--dry-run", action="store_true", help="Проверка без постинга")
    parser.add_argument("--force", action="store_true", help="Игнорировать done/lock и лимиты частоты")
    parser.add_argument("--skip-schema-check", action="store_true", help="Пропустить проверку схемы")
    args = parser.parse_args()

    pp.run_schema_check(args.skip_schema_check)

    groups = all_enabled_groups(cfg, args.group)
    if not groups:
        print("Нет включённых групп в config/fb_groups.json / fb_groups_list.txt", file=sys.stderr)
        return 1

    page_ids: list[str] = []
    if args.queue:
        database_id = os.environ.get("NOTION_DATABASE_ID") or os.environ.get("NOTION_DB_ID")
        if not database_id:
            print("Задай NOTION_DB_ID в .env", file=sys.stderr)
            return 1
        fields = cfg["notion"]["fields"]
        pages = pp.notion_query_ready(
            database_id,
            cfg["notion"]["statuses"]["ready"],
            fields["status"],
            locked_field=fields["fb_groups_locked"],
            done_field=fields.get("fb_groups_done") or "phone_fb_groups_done",
        )
        if not pages:
            print("Очередь пуста.")
            return 0
        pages = sort_queue_resume_first(pages, cfg, groups, fields)
        page_ids = [p["id"] for p in pages]
    elif args.page_id:
        page_ids = [args.page_id]
    else:
        parser.error("--page-id или --queue")

    exit_code = 0
    waiting = len(page_ids)
    for pid in page_ids:
        print(f"\n--- {pid} ---")
        try:
            out = publish_object(
                pid,
                cfg,
                groups=groups,
                dry_run=args.dry_run,
                force=args.force,
                waiting_objects=waiting,
            )
            print(json.dumps(out, indent=2, ensure_ascii=False))
            if out.get("skipped"):
                reason = str(out.get("reason") or "")
                if should_stop_fb_queue(reason):
                    break
                continue
            break
        except ProfileBusy as e:
            print(f"ERROR {pid}: {e}", file=sys.stderr)
            return 3
        except Exception as e:
            print(f"ERROR {pid}: {e}", file=sys.stderr)
            exit_code = 1
            break
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
