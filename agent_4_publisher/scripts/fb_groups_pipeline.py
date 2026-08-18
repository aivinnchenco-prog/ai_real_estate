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

USER_AGENT_TAG = "real-estate-agent4-fb-groups/1.0"

# Плейсхолдер композера в шапке ленты группы (EN/RU/UK)
COMPOSER_RE = re.compile(
    r"write something|напишите что|напишіть|что у вас|що у вас|anything else|"
    r"поделитесь|поділіться|discuss",
    re.I,
)
POST_BUTTON_RE = re.compile(
    r"post|publish|опублик|отправ|send|надісл|відправ",
    re.I,
)
POST_BUTTON_EXCLUDE_RE = re.compile(
    r"cancel|отмен|скас|photo|фото|закры|close|назад|back|далее|next|далі",
    re.I,
)
NEXT_BUTTON_RE = re.compile(r"^(далі|next|далее|done|готово|ok)$", re.I)
PHOTO_BUTTON_RE = re.compile(r"photo|фото|зображення|image", re.I)
JOIN_BUTTON_RE = re.compile(
    r"^(join(\s+group)?|вступить(\s+в\s+группу)?|приєднатися(\s+до\s+групи)?|"
    r"присоединиться(\s+к\s+группе)?|become a member)$",
    re.I,
)
MEMBER_BUTTON_RE = re.compile(
    r"^(в группе|joined|приєднались|member|участник|joined group)$",
    re.I,
)
PENDING_JOIN_RE = re.compile(
    r"cancel request|отменить запрос|скасувати запит|заявка отправлена|"
    r"request sent|запрос отправлен|на рассмотрении|очікує|pending approval",
    re.I,
)
# Кнопка отправки в диалоге заявки на вступление (правила группы / вопросы)
JOIN_SUBMIT_RE = re.compile(
    r"надіслати|подати запит|відправити|submit|send request|отправить|подать запит|"
    r"подать заявку|готово|done|ok",
    re.I,
)
JOIN_DIALOG_HINT_RE = re.compile(
    r"правил|rules|terms|погодж|question|питан|вступ|join|member|участ|заявк|request",
    re.I,
)
JOIN_RULES_LABEL_RE = re.compile(
    r"правил|rules|terms|погодж|accept|приймаю|згоден|agree",
    re.I,
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


def check_rate_limits(cfg: dict[str, Any], group_url: str) -> str | None:
    """None — можно постить, иначе причина отказа."""
    limits = cfg.get("limits", {})
    now = datetime.now(timezone.utc)
    posts = load_state(cfg).get("posts", [])

    def ts(entry: dict[str, Any]) -> datetime:
        return datetime.fromisoformat(entry["ts"])

    day_ago = now - timedelta(days=1)
    last_day = [p for p in posts if ts(p) > day_ago]
    max_day = int(limits.get("max_posts_per_day_total", 8))
    if len(last_day) >= max_day:
        return f"дневной лимит {max_day} постов исчерпан"

    gap = int(limits.get("min_minutes_between_posts", 45))
    recent = [p for p in posts if ts(p) > now - timedelta(minutes=gap)]
    if recent:
        return f"минимальный интервал {gap} мин не выдержан (последний пост {recent[-1]['ts']})"

    group_gap = int(limits.get("min_minutes_between_posts_same_group", 1440))
    same_group = [
        p for p in posts if p["group"] == group_url and ts(p) > now - timedelta(minutes=group_gap)
    ]
    if same_group:
        return f"в эту группу уже постили за последние {group_gap} мин"
    return None


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

def human_delay(cfg: dict[str, Any], factor: float = 1.0) -> None:
    hd = cfg.get("browser", {}).get("human_delay_ms", {})
    lo = int(hd.get("min", 900)) / 1000.0
    hi = int(hd.get("max", 2400)) / 1000.0
    time.sleep(random.uniform(lo, hi) * factor)


def idle_scroll(page: Any, cfg: dict[str, Any], seconds_range: tuple[int, int] = (15, 45)) -> None:
    """«Полистать ленту»: человек не открывает FB и сразу не жмёт «Опубликовать».
    Случайные скроллы вниз (изредка вверх) с паузами на «чтение»."""
    total = random.uniform(*seconds_range)
    deadline = time.time() + total
    try:
        while time.time() < deadline:
            delta = random.randint(300, 1200)
            if random.random() < 0.15:
                delta = -random.randint(200, 600)
            page.mouse.wheel(0, delta)
            time.sleep(random.uniform(1.5, 6.0))
    except Exception:
        pass  # прогрев не должен ронять постинг


def type_like_human(page: Any, text: str, cfg: dict[str, Any]) -> None:
    """Ввод текста кусками по абзацам с паузами «на подумать» — вместо
    мгновенной вставки всего поста одним куском."""
    chunks = [c for c in re.split(r"(\n+)", text) if c]
    for chunk in chunks:
        page.keyboard.insert_text(chunk)
        time.sleep(random.uniform(0.4, 1.6))


def pause_between_groups(cfg: dict[str, Any]) -> None:
    """Длинная пауза между группами (минуты): одинаковый пост в несколько
    групп за минуту — главный маркер спам-бота."""
    lo, hi = cfg.get("limits", {}).get("minutes_between_groups", [8, 18])
    minutes = random.uniform(float(lo), float(hi))
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
    }
    proxy = resolve_proxy(cfg)
    if proxy:
        launch_kwargs["proxy"] = proxy
    context = p.chromium.launch_persistent_context(str(profile_path(cfg)), **launch_kwargs)
    context.set_default_timeout(int(browser.get("action_timeout_ms", 30000)))
    return context


def is_logged_in(context: Any) -> bool:
    return any(
        c.get("name") == "c_user" and "facebook.com" in c.get("domain", "")
        for c in context.cookies()
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
    page.fill('input[name="email"], input#email', email)
    human_delay(cfg)
    page.fill('input[name="pass"], input#pass', password)
    human_delay(cfg)
    page.locator('button[name="login"], button[type="submit"]').first.click()
    page.wait_for_load_state("domcontentloaded", timeout=90000)
    human_delay(cfg, 2.0)
    if "checkpoint" in page.url.lower():
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


def _join_dialog_candidate(dialog: Any) -> bool:
    """Диалог заявки на вступление (не композер поста)."""
    try:
        if not dialog.is_visible():
            return False
        if dialog.locator('div[role="textbox"][contenteditable="true"]').count():
            return False
        body = dialog.inner_text(timeout=2000) or ""
    except Exception:
        return False
    if not body.strip():
        return False
    if JOIN_DIALOG_HINT_RE.search(body):
        return True
    return bool(
        dialog.locator('[role="checkbox"]').count()
        or dialog.locator('input[type="checkbox"]').count()
    )


def _accept_join_dialog_checkboxes(dialog: Any, cfg: dict[str, Any]) -> int:
    clicked = 0
    for selector in (
        '[role="checkbox"][aria-checked="false"]',
        '[role="checkbox"][aria-checked="mixed"]',
        'input[type="checkbox"]:not(:checked)',
    ):
        boxes = dialog.locator(selector)
        for i in range(boxes.count()):
            box = boxes.nth(i)
            try:
                box.click(timeout=2000)
                clicked += 1
                human_delay(cfg, 0.4)
            except Exception:
                try:
                    box.click(force=True, timeout=2000)
                    clicked += 1
                    human_delay(cfg, 0.4)
                except Exception:
                    continue

    labels = dialog.locator("label").filter(has_text=JOIN_RULES_LABEL_RE)
    for i in range(labels.count()):
        label = labels.nth(i)
        try:
            label.click(timeout=1500)
            clicked += 1
            human_delay(cfg, 0.35)
        except Exception:
            continue
    return clicked


def _fill_join_dialog_inputs(dialog: Any, cfg: dict[str, Any]) -> int:
    """Заполнить обязательные поля вопросов (короткий нейтральный ответ)."""
    filled = 0
    filler = "Phuket, Thailand"
    fields = dialog.locator(
        "textarea:visible, input[type='text']:visible, "
        "input[type='search']:visible, div[role='textbox'][contenteditable='true']:visible"
    )
    for i in range(fields.count()):
        field = fields.nth(i)
        try:
            if not field.is_visible():
                continue
            tag = (field.evaluate("el => el.tagName") or "").lower()
            existing = ""
            if tag == "div":
                existing = (field.inner_text(timeout=1000) or "").strip()
            else:
                existing = (field.input_value(timeout=1000) or "").strip()
            if existing:
                continue
            field.click(timeout=1500)
            human_delay(cfg, 0.3)
            if tag == "div":
                field.type(filler, delay=30)
            else:
                field.fill(filler)
            filled += 1
            human_delay(cfg, 0.4)
        except Exception:
            continue
    return filled


def _click_join_dialog_submit(dialog: Any, page: Any, cfg: dict[str, Any]) -> bool:
    try:
        dialog.evaluate("el => { el.scrollTop = el.scrollHeight; }")
    except Exception:
        pass

    candidates: list[Any] = []
    for loc in (
        dialog.get_by_role("button", name=JOIN_SUBMIT_RE),
        dialog.locator('[role="button"]').filter(has_text=JOIN_SUBMIT_RE),
        dialog.locator('[type="submit"]'),
    ):
        for i in range(loc.count()):
            candidates.append(loc.nth(i))

    seen: set[str] = set()
    for btn in candidates:
        try:
            label = (btn.inner_text(timeout=800) or btn.get_attribute("aria-label") or "").strip()
            key = label.lower()
            if not key or key in seen:
                continue
            seen.add(key)
            if btn.get_attribute("aria-disabled") in ("true", "1"):
                continue
            btn.scroll_into_view_if_needed(timeout=2000)
            human_delay(cfg, 0.3)
            btn.click(timeout=3000)
            return True
        except Exception:
            try:
                btn.click(force=True, timeout=2000)
                return True
            except Exception:
                continue
    return False


def handle_join_request_dialog(page: Any, cfg: dict[str, Any]) -> bool:
    """Диалог «Запросы на участие»: чекбоксы, вопросы, кнопка «Отправить»."""
    deadline = time.time() + 35.0
    submitted = False
    while time.time() < deadline:
        dialogs = page.locator('div[role="dialog"]')
        acted = False
        for i in range(dialogs.count()):
            dialog = dialogs.nth(i)
            if not _join_dialog_candidate(dialog):
                continue
            _accept_join_dialog_checkboxes(dialog, cfg)
            _fill_join_dialog_inputs(dialog, cfg)
            if _click_join_dialog_submit(dialog, page, cfg):
                submitted = True
                acted = True
                human_delay(cfg, 2.0)
                break
            acted = True
        if submitted:
            remaining = [
                d
                for j in range(page.locator('div[role="dialog"]').count())
                if _join_dialog_candidate(page.locator('div[role="dialog"]').nth(j))
            ]
            if not remaining:
                return True
        if not acted:
            return submitted
        time.sleep(0.8)
    if not submitted:
        debug_screenshot(page, "join_dialog_stuck")
    return submitted


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
                close_btn.first.click()
            else:
                page.keyboard.press("Escape")
        except Exception:
            page.keyboard.press("Escape")
        human_delay(cfg)


def _join_request_pending(page: Any) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=3000) or ""
    except Exception:
        body = ""
    if PENDING_JOIN_RE.search(body):
        return True
    cancel = page.get_by_role(
        "button", name=re.compile(r"cancel request|отменить запрос|скасувати", re.I)
    )
    return cancel.count() > 0


def _find_join_button(page: Any) -> Any | None:
    """Кнопка/ссылка «Вступить» в шапке или в main (не «В группе»)."""
    scopes = (
        page.locator('[data-pagelet="GroupCover"]'),
        page.locator('[role="banner"]'),
        page.locator('[role="main"]'),
        page,
    )
    for scope in scopes:
        for role in ("button", "link"):
            loc = scope.get_by_role(role, name=JOIN_BUTTON_RE)
            for i in range(loc.count()):
                candidate = loc.nth(i)
                try:
                    label = (candidate.inner_text(timeout=500) or "").strip()
                    if label and MEMBER_BUTTON_RE.match(label):
                        continue
                except Exception:
                    pass
                return candidate
    return None


def _is_group_member(page: Any) -> bool:
    if _join_request_pending(page):
        return False
    if _find_join_button(page):
        return False
    composer = page.locator('[role="main"] [role="button"]', has_text=COMPOSER_RE)
    if composer.count():
        return True
    joined = page.get_by_role("button", name=MEMBER_BUTTON_RE)
    if joined.count():
        return True
    try:
        body = (page.locator("body").inner_text(timeout=3000) or "").lower()
    except Exception:
        body = ""
    return any(
        marker in body
        for marker in (
            "в группе",
            "joined group",
            "участник группы",
            "you're a member",
            "ви учасник",
        )
    )


def maybe_join_group(page: Any, cfg: dict[str, Any]) -> bool:
    """Если аккаунт не в группе — жмём «Вступить» и отправляем заявку при необходимости."""
    if _is_group_member(page):
        return False
    if _join_request_pending(page):
        return True

    join = _find_join_button(page)
    if not join:
        try:
            page.evaluate("window.scrollTo(0, 0)")
            human_delay(cfg, 1.0)
        except Exception:
            pass
        join = _find_join_button(page)
    if not join:
        return False

    try:
        join.click()
    except Exception:
        join.click(force=True)
    human_delay(cfg, 3.0)
    for _ in range(3):
        if handle_join_request_dialog(page, cfg):
            break
        if not page.locator('div[role="dialog"]').count():
            break
        human_delay(cfg, 1.5)
    human_delay(cfg, 2.0)

    deadline = time.time() + 20.0
    while time.time() < deadline:
        if _is_group_member(page):
            page.reload(wait_until="domcontentloaded")
            human_delay(cfg, 2.0)
            return True
        if _join_request_pending(page):
            return True
        if not _find_join_button(page):
            page.reload(wait_until="domcontentloaded")
            human_delay(cfg, 2.0)
            return True
        time.sleep(1.0)

    page.reload(wait_until="domcontentloaded")
    human_delay(cfg, 2.0)
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
    composer.first.click()
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
            photo_btn.first.click()
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


def advance_after_photos(page: Any, dialog: Any, cfg: dict[str, Any]) -> Any:
    """После загрузки фото FB может показать «Далее» перед финальным экраном поста."""
    current_dialog = dialog
    for _ in range(4):
        clicked = False
        for scope in (current_dialog, page):
            btn = scope.get_by_role("button", name=NEXT_BUTTON_RE)
            if not btn.count():
                btn = scope.locator('[role="button"]').filter(has_text=NEXT_BUTTON_RE)
            if btn.count():
                candidate = btn.first
                if candidate.get_attribute("aria-disabled") not in ("true", "1"):
                    try:
                        candidate.scroll_into_view_if_needed(timeout=2000)
                        candidate.click(timeout=3000)
                        clicked = True
                        human_delay(cfg, 1.5)
                        break
                    except Exception:
                        try:
                            candidate.click(force=True, timeout=2000)
                            clicked = True
                            human_delay(cfg, 1.5)
                            break
                        except Exception:
                            pass
        if not clicked:
            break
        refreshed = page.locator(
            'div[role="dialog"]',
            has=page.locator('div[role="textbox"][contenteditable="true"]'),
        )
        if refreshed.count():
            current_dialog = refreshed.last
    return current_dialog


def _iter_post_button_candidates(dialog: Any, page: Any) -> list[Any]:
    candidates: list[Any] = []
    seen: set[str] = set()
    scopes = (dialog, page.locator('div[role="dialog"]').last, page)
    for scope in scopes:
        try:
            if hasattr(scope, "count") and scope.count() == 0:
                continue
        except Exception:
            pass
        locators = (
            scope.get_by_role("button", name=POST_BUTTON_RE),
            scope.locator('[role="button"]').filter(has_text=POST_BUTTON_RE),
        )
        for loc in locators:
            for i in range(loc.count()):
                btn = loc.nth(i)
                try:
                    label = (
                        btn.inner_text(timeout=500)
                        or btn.get_attribute("aria-label")
                        or ""
                    ).strip()
                except Exception:
                    label = ""
                key = label.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                candidates.append(btn)
    return candidates


def click_post(page: Any, dialog: Any, cfg: dict[str, Any]) -> None:
    deadline = time.time() + int(cfg.get("browser", {}).get("upload_wait_ms", 60000)) / 1000.0
    while time.time() < deadline:
        for btn in _iter_post_button_candidates(dialog, page):
            try:
                label = (
                    btn.inner_text(timeout=500) or btn.get_attribute("aria-label") or ""
                ).strip()
            except Exception:
                label = ""
            if label and POST_BUTTON_EXCLUDE_RE.search(label):
                continue
            if label and not POST_BUTTON_RE.search(label):
                continue
            if btn.get_attribute("aria-disabled") in ("true", "1"):
                continue
            try:
                btn.scroll_into_view_if_needed(timeout=2000)
                human_delay(cfg, 0.5)
                btn.click(timeout=3000)
                return
            except Exception:
                try:
                    btn.click(force=True, timeout=2000)
                    return
                except Exception:
                    continue
        time.sleep(1.0)
    raise RuntimeError(
        "POST_BUTTON_DISABLED: кнопка «Опубликовать/Отправить» не активировалась. "
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
    # Полистать ленту группы перед постингом — как живой участник
    idle_scroll(page, cfg, (10, 30))
    page.keyboard.press("Home")
    human_delay(cfg)

    # Вступаем если можно; даже при ожидании одобрения — пробуем постить (публичные группы)
    maybe_join_group(page, cfg)

    # Приветственные оверлеи новой группы перекрывают композер
    close_blocking_dialogs(page, cfg)

    before = collect_post_hrefs(page)
    dialog = open_composer(page, cfg)
    human_delay(cfg)

    textbox = dialog.locator('div[role="textbox"][contenteditable="true"]').first
    textbox.click()
    human_delay(cfg)
    type_like_human(page, caption, cfg)
    human_delay(cfg)

    attach_photos(page, dialog, files, cfg)
    human_delay(cfg, 1.5)
    dialog = advance_after_photos(page, dialog, cfg)
    human_delay(cfg, 1.0)

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


def target_groups(cfg: dict[str, Any], override_url: str | None) -> list[dict[str, Any]]:
    if override_url:
        return [{"name": override_url, "url": override_url, "enabled": True}]
    groups = [g for g in cfg.get("groups", []) if g.get("enabled", True)]
    groups += load_groups_file(cfg)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for g in groups:
        key = g["url"].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        unique.append(g)
    # Перемешиваем: постинг всегда в одном и том же порядке — маркер бота
    random.shuffle(unique)
    max_groups = int(cfg.get("limits", {}).get("max_groups_per_run", 3))
    return unique[:max_groups]


def publish_object(
    page_id: str,
    cfg: dict[str, Any],
    *,
    groups: list[dict[str, Any]],
    dry_run: bool,
    force: bool,
) -> dict[str, Any]:
    fields = cfg["notion"]["fields"]
    status_ready = cfg["notion"]["statuses"]["ready"]

    page = pp.notion_get_page(page_id)
    object_id = pp.get_prop(page, fields["object_id"], "rich_text") or ""
    status = pp.get_prop(page, fields["status"], "status")
    caption = pp.get_prop(page, fields["caption"], "rich_text") or ""
    gallery_url = pp.get_prop(page, fields["photo"], "url") or ""
    locked = bool(pp.get_prop(page, fields["fb_groups_locked"], "checkbox"))

    result: dict[str, Any] = {
        "page_id": page_id,
        "object_id": object_id,
        "status": status,
        "groups": [g["url"] for g in groups],
        "caption_len": len(caption),
        "caption_preview": caption[:120],
    }

    if locked and not force:
        return {**result, "skipped": True, "reason": "fb_groups_locked"}
    if status != status_ready and not force:
        return {**result, "skipped": True, "reason": f"status={status}, ожидался {status_ready}"}
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
        for group in groups:
            reason = check_rate_limits(cfg, group["url"])
            if reason:
                return {**result, "skipped": True, "reason": f"лимит: {reason}"}

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

                # Ошибка в одной группе (JOIN_PENDING и т.п.) не блокирует остальные
                for i, group in enumerate(groups):
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
                # Не закрывать браузер сразу после «Опубликовать»
                idle_scroll(bpage, cfg, (10, 25))
            finally:
                context.close()
                guard.record_session_end(branch="fb_groups")
        if not submitted:
            errors = "; ".join(f"{p['group_url']}: {p.get('error')}" for p in posted)
            raise RuntimeError(f"ни в одну группу не запостилось — {errors}")
    except Exception as e:
        # До реальной отправки — снимаем лок, чтобы можно было перезапустить
        if not submitted:
            pp.notion_update_fields(
                page_id, {fields["fb_groups_locked"]: pp.notion_checkbox_property(False)}
            )
        mark_failure(page_id, page, fields, f"fb_groups: {e}")
        raise
    finally:
        if lock_file:
            lock_file.unlink(missing_ok=True)

    result["posted"] = posted

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    log_lines = []
    saved_urls: list[str] = []
    for out in posted:
        if out.get("post_url"):
            saved_urls.append(out["post_url"])
            log_lines.append(f"{now} {out['group_url']} -> {out['post_url']}")
        elif out.get("pending_approval"):
            log_lines.append(f"{now} {out['group_url']} -> pending_approval")
        elif out.get("error"):
            log_lines.append(f"{now} {out['group_url']} -> ERROR: {out['error'][:120]}")
        else:
            log_lines.append(f"{now} {out['group_url']} -> опубликовано, url не найден")

    props: dict[str, Any] = {
        fields["publish_error"]: {"rich_text": []},
        **append_fb_groups_log(page, fields, "\n".join(log_lines)),
    }
    if saved_urls:
        props.update(append_post_urls(page, fields, saved_urls))
    pp.notion_update_fields(page_id, props)
    result["saved_urls"] = saved_urls
    return result


def main() -> int:
    pp.load_dotenv()
    cfg = load_fb_groups_config()

    parser = argparse.ArgumentParser(description="Notion CRM → группы Facebook (Playwright)")
    parser.add_argument("--page-id", help="Notion page ID объекта")
    parser.add_argument("--queue", action="store_true", help="Все объекты в ready_to_post")
    parser.add_argument("--group", help="URL группы (перекрывает config/fb_groups.json)")
    parser.add_argument("--dry-run", action="store_true", help="Проверка без постинга")
    parser.add_argument("--force", action="store_true", help="Игнорировать lock и лимиты частоты")
    parser.add_argument("--skip-schema-check", action="store_true", help="Пропустить проверку схемы")
    args = parser.parse_args()

    pp.run_schema_check(args.skip_schema_check)

    groups = target_groups(cfg, args.group)
    if not groups:
        print("Нет включённых групп в config/fb_groups.json", file=sys.stderr)
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
        )
        if not pages:
            print("Очередь пуста.")
            return 0
        page_ids = [p["id"] for p in pages]
    elif args.page_id:
        page_ids = [args.page_id]
    else:
        parser.error("--page-id или --queue")

    exit_code = 0
    for pid in page_ids:
        print(f"\n--- {pid} ---")
        try:
            out = publish_object(
                pid, cfg, groups=groups, dry_run=args.dry_run, force=args.force
            )
            print(json.dumps(out, indent=2, ensure_ascii=False))
        except ProfileBusy as e:
            print(f"ERROR {pid}: {e}", file=sys.stderr)
            return 3
        except Exception as e:
            print(f"ERROR {pid}: {e}", file=sys.stderr)
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
