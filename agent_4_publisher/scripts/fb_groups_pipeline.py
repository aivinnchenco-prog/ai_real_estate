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

USER_AGENT_TAG = "real-estate-agent4-fb-groups/1.0"

# Плейсхолдер композера в шапке ленты группы (EN/RU/UK)
COMPOSER_RE = re.compile(
    r"write something|напишите что|напишіть|что у вас|що у вас|anything else|"
    r"поделитесь|поділіться|discuss",
    re.I,
)
POST_BUTTON_RE = re.compile(r"^(post|опубликовать|опублікувати|publish)$", re.I)
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


def open_browser(p: Any, cfg: dict[str, Any]) -> Any:
    browser = cfg.get("browser", {})
    viewport = browser.get("viewport", {"width": 1440, "height": 900})
    context = p.chromium.launch_persistent_context(
        str(profile_path(cfg)),
        headless=resolve_headless(),
        viewport={"width": int(viewport["width"]), "height": int(viewport["height"])},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    context.set_default_timeout(int(browser.get("action_timeout_ms", 30000)))
    return context


def is_logged_in(context: Any) -> bool:
    return any(
        c.get("name") == "c_user" and "facebook.com" in c.get("domain", "")
        for c in context.cookies()
    )


def try_relogin(page: Any, cfg: dict[str, Any]) -> None:
    """Запасной вариант из набора: разовый логин по FB_EMAIL/FB_PASSWORD."""
    email = os.environ.get("FB_EMAIL", "").strip()
    password = os.environ.get("FB_PASSWORD", "").strip()
    if not email or not password:
        raise RuntimeError("AUTH_REQUIRED: сессия протухла, а FB_EMAIL/FB_PASSWORD не заданы")
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
            boxes.nth(i).click()
            human_delay(cfg, 0.5)
        except Exception:
            continue

    submit = dialog.get_by_role("button", name=JOIN_SUBMIT_RE)
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if submit.count():
            btn = submit.first
            if btn.get_attribute("aria-disabled") not in ("true", "1"):
                btn.click()
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
                close_btn.first.click()
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
    join.first.click()
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


def click_post(page: Any, dialog: Any, cfg: dict[str, Any]) -> None:
    post_btn = dialog.get_by_role("button", name=POST_BUTTON_RE)
    deadline = time.time() + int(cfg.get("browser", {}).get("upload_wait_ms", 60000)) / 1000.0
    while time.time() < deadline:
        if post_btn.count():
            btn = post_btn.first
            if btn.get_attribute("aria-disabled") not in ("true", "1"):
                human_delay(cfg)
                btn.click()
                return
        time.sleep(1.0)
    raise RuntimeError("POST_BUTTON_DISABLED: кнопка «Опубликовать» не активировалась")


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
    textbox.click()
    human_delay(cfg)
    page.keyboard.insert_text(caption)
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

                # Ошибка в одной группе (JOIN_PENDING и т.п.) не блокирует остальные
                for group in groups:
                    try:
                        out = post_to_group(bpage, group["url"], caption, files, cfg)
                        submitted = True
                        out["group_url"] = group["url"]
                        posted.append(out)
                        record_post(cfg, group["url"], object_id, out.get("post_url") or "")
                    except Exception as e:
                        posted.append({"group_url": group["url"], "error": str(e)})
                    human_delay(cfg, 3.0)
            finally:
                context.close()
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
