from __future__ import annotations

import html
import re
import shutil
import subprocess
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from .ui import click_text_or_desc, ensure_unlocked, human_pause, pass_threads_human_check

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_TIKTOK_POST_RE = re.compile(
    r"/(?P<kind>video|photo)/(?P<post_id>\d{15,22})(?:[/?#]|$)",
    re.IGNORECASE,
)
_TIKTOK_DEEP_ID_RE = re.compile(
    r"(?:aweme/detail/|(?:aweme|item|video|photo)_id\s*[=:]\s*)"
    r"(?P<post_id>\d{15,22})",
    re.IGNORECASE,
)
_TIKTOK_LOG_UNIQUE_ID_RE = re.compile(r"\[uniqueId=(?P<post_id>\d{15,22})\]")
_TIKTOK_LONG_PRESS_MS = 4000
_TIKTOK_ID_KEYS = ("share_item_id", "item_id", "aweme_id", "video_id")
_CLIPBOARD_UNAVAILABLE = (
    "no shell command implementation",
    "unknown command",
    "exception",
    "permission denial",
    "securityexception",
)
_CLIPBOARD_SENTINEL = "__PUBLISHER_LINK_PENDING__"
_INSTAGRAM_IMMEDIATE_AGE_RE = re.compile(
    r"^\s*(?:только\s+что|сейчас|just\s+now|now)\s*$",
    re.IGNORECASE,
)
_INSTAGRAM_RELATIVE_AGE_RE = re.compile(
    r"^\s*(?P<count>\d{1,3})\s*"
    r"(?P<unit>с|сек(?:унд[аы]?)?\.?|sec(?:ond)?s?|"
    r"м|мин(?:ут[аы]?)?\.?|min(?:ute)?s?)"
    r"(?:\s+назад|\s+ago)?\s*$",
    re.IGNORECASE,
)


def _read_clipboard(d) -> str:
    try:
        text = d.clipboard or ""
        if text:
            return text.strip()
    except Exception:
        pass
    try:
        out = d.shell("cmd clipboard get").output or ""
        return out.strip()
    except Exception:
        return ""


def _clear_clipboard(d) -> None:
    termux_set = shutil.which("termux-clipboard-set")
    if termux_set:
        try:
            subprocess.run(
                [termux_set, _CLIPBOARD_SENTINEL],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            return
        except Exception:
            pass
    try:
        d.shell(f"cmd clipboard set {_CLIPBOARD_SENTINEL}")
    except Exception:
        pass


def _first_url(text: str, patterns: tuple[str, ...]) -> str | None:
    if not text:
        return None
    for match in _URL_RE.findall(text):
        url = match.rstrip(").,]")
        if any(p in url for p in patterns):
            return url
    return None


def _tap_copy_link(d, android_cfg: dict[str, Any]) -> bool:
    labels = (
        "Копировать ссылку",
        "Коп. ссылку",
        "Коп ссылку",
        "Copy link",
        "Скопировать ссылку",
        "Copy Link",
        "Copy to clipboard",
        "Копировать в буфер",
        "Копировать",
        "Copy",
    )
    for label in labels:
        if click_text_or_desc(d, label, timeout=1.0):
            human_pause(android_cfg, scale=0.5)
            return True
        if d(textContains="ссылк").exists(timeout=0.3):
            d(textContains="ссылк").click()
            human_pause(android_cfg, scale=0.5)
            return True
        if d(descriptionContains="ссылк").exists(timeout=0.3):
            d(descriptionContains="ссылк").click()
            human_pause(android_cfg, scale=0.5)
            return True
    # Samsung/YouTube share: «Коп. ссылку» часто без a11y-текста — ряд под иконками приложений
    w, h = d.window_size()
    for y_ratio in (0.78, 0.82, 0.74, 0.86):
        d.click(int(w * 0.35), int(h * y_ratio))
        time.sleep(0.6)
        clip = _read_clipboard(d)
        if clip and ("http://" in clip or "https://" in clip):
            return True
    return False


def _read_clipboard_robust(d) -> str:
    """
    Читать clipboard несколькими независимыми Android API.

    На Android 13+ shell часто не имеет права читать clipboard, хотя вставка
    в foreground-приложение разрешена. Поэтому вызывающий TikTok-код имеет
    дополнительный paste-probe; здесь собираем только неинвазивные источники.
    """
    candidates: list[str] = []
    termux_clipboard = shutil.which("termux-clipboard-get")
    if termux_clipboard:
        try:
            result = subprocess.run(
                [termux_clipboard],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if result.stdout.strip():
                candidates.append(result.stdout.strip())
        except Exception:
            pass
    try:
        val = d.clipboard
        if isinstance(val, str) and val.strip():
            candidates.append(val.strip())
    except Exception:
        pass
    for command in (
        "cmd clipboard get",
        "cmd clipboard get-primary-clip",
        "dumpsys clipboard",
    ):
        try:
            result = d.shell(command)
            out = getattr(result, "output", result) or ""
            if isinstance(out, str) and out.strip():
                candidates.append(out.strip())
        except Exception:
            pass
    # Некоторые версии TikTok/Android ненадолго оставляют URL в toast/a11y.
    try:
        candidates.append(html.unescape(d.dump_hierarchy()))
    except Exception:
        pass
    for value in candidates:
        lowered = value.lower()
        if any(marker in lowered for marker in _CLIPBOARD_UNAVAILABLE):
            continue
        if "http://" in value or "https://" in value:
            return value
    return next(
        (
            value
            for value in candidates
            if value and not any(marker in value.lower() for marker in _CLIPBOARD_UNAVAILABLE)
        ),
        "",
    )


def _open_share_and_copy(
    d,
    android_cfg: dict[str, Any],
    *,
    share_labels: tuple[str, ...] = ("Поделиться", "Share", "Отправить"),
    url_patterns: tuple[str, ...],
) -> str | None:
    _clear_clipboard(d)
    opened = False
    for label in share_labels:
        if click_text_or_desc(d, label, timeout=1.5):
            opened = True
            break
        if d(descriptionContains=label).exists(timeout=0.4):
            d(descriptionContains=label).click()
            opened = True
            break
    if not opened:
        return None
    human_pause(android_cfg, scale=0.7)
    if not _tap_copy_link(d, android_cfg):
        d.press("back")
        time.sleep(0.4)
        return None
    human_pause(android_cfg, scale=0.4)
    url = _first_url(_read_clipboard_robust(d), url_patterns)
    if d(text="Отмена").exists(timeout=0.4):
        d(text="Отмена").click()
    elif d(text="Cancel").exists(timeout=0.3):
        d(text="Cancel").click()
    else:
        d.press("back")
    time.sleep(0.3)
    return url


def _is_tiktok_host(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host == "tiktok.com" or host.endswith(".tiktok.com")


def _first_tiktok_url(text: str) -> str | None:
    for match in _URL_RE.findall(html.unescape(text or "")):
        candidate = match.rstrip(").,]}")
        if _is_tiktok_host(candidate):
            return candidate
    return None


def _tiktok_post_parts(url: str | None) -> tuple[str, int] | None:
    """Вернуть (video|photo, TikTok snowflake id) только для TikTok URL."""
    if not url or not _is_tiktok_host(url):
        return None
    parsed = urlparse(html.unescape(url))
    match = _TIKTOK_POST_RE.search(parsed.path)
    if match:
        return match.group("kind").lower(), int(match.group("post_id"))
    query = parse_qs(parsed.query)
    for key in _TIKTOK_ID_KEYS:
        values = query.get(key) or ()
        if values and str(values[0]).isdigit():
            # Query-only URL не доказывает тип контента.
            return "unknown", int(values[0])
    return None


def _tiktok_url_is_fresh_target(
    url: str | None,
    channel: str,
    *,
    now: float | None = None,
    max_age_seconds: float = 7200.0,
) -> bool:
    """
    Проверить не просто домен, а тип и время создания TikTok-публикации.

    TikTok post id содержит Unix timestamp в старших 32 битах. Это позволяет
    отбрасывать закреплённые/старые посты даже при ошибочном выборе тайла.
    """
    parts = _tiktok_post_parts(url)
    if not parts:
        return False
    kind, post_id = parts
    expected_kind = "photo" if channel == "tiktok_carousel" else "video"
    if kind != expected_kind:
        return False
    created_at = post_id >> 32
    current = time.time() if now is None else float(now)
    # До пяти минут допускается рассинхронизация часов телефона/сервера.
    return current - max_age_seconds <= created_at <= current + 300.0


def _canonical_tiktok_url(url: str) -> str:
    """Убрать tracking query, оставив проверенный canonical post path."""
    parsed = urlparse(html.unescape(url))
    return parsed._replace(query="", fragment="").geturl()


def _is_tiktok_short_url(url: str | None) -> bool:
    if not url or not _is_tiktok_host(url):
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return host in ("vm.tiktok.com", "vt.tiktok.com") and bool(
        parsed.path.strip("/")
    )


def _resolve_tiktok_short_url(url: str, *, timeout: float = 8.0) -> str | None:
    """Развернуть vm/vt.tiktok.com без стороннего API."""
    if _tiktok_post_parts(url):
        return _canonical_tiktok_url(url)
    if not _is_tiktok_host(url):
        return None
    response = None
    try:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                    "Chrome/124 Mobile Safari/537.36"
                )
            },
        )
        response = urlopen(request, timeout=timeout)
    except HTTPError as error:
        # TikTok может успешно развернуть short URL, а canonical page затем
        # вернуть 403 bot-защиты. Финальный redirect URL всё равно достоверен.
        response = error
    except Exception:
        return None
    try:
        with response:
            final_url = response.geturl()
            if _tiktok_post_parts(final_url):
                return _canonical_tiktok_url(final_url)
            body = response.read(512_000).decode("utf-8", errors="ignore")
    except Exception:
        return None
    decoded = html.unescape(body)
    for candidate in _URL_RE.findall(decoded):
        candidate = candidate.rstrip(").,]}")
        if _tiktok_post_parts(candidate):
            return _canonical_tiktok_url(candidate)
    return None


def _tiktok_url_from_sources(d, *, channel: str | None = None) -> str | None:
    """Найти TikTok URL в clipboard, toast/a11y или activity diagnostics."""
    sources: list[str] = [_read_clipboard_robust(d)]
    for command in ("dumpsys activity top", "dumpsys activity activities"):
        try:
            result = d.shell(command)
            sources.append(str(getattr(result, "output", result) or ""))
        except Exception:
            pass
    for source in sources:
        candidate = _first_tiktok_url(source)
        if candidate:
            return candidate
    # Некоторые TikTok-сборки не кладут URL в a11y/clipboard, но оставляют
    # точный aweme/item id открытого поста в Activity intent.
    if channel in ("tiktok", "tiktok_carousel"):
        kind = "photo" if channel == "tiktok_carousel" else "video"
        for source in sources:
            match = _TIKTOK_DEEP_ID_RE.search(source or "")
            if match:
                return (
                    f"https://www.tiktok.com/@_/{kind}/"
                    f"{match.group('post_id')}"
                )
    return None


def _tiktok_paste_probe(d, android_cfg: dict[str, Any]) -> str | None:
    """
    Fallback для Android privacy: вставить clipboard в поиск TikTok и прочитать
    EditText через accessibility. Запрос не отправляется, затем экран закрывается.
    """
    def _open_search() -> bool:
        for label in ("Поиск", "Search"):
            if click_text_or_desc(d, label, timeout=1.0):
                return True
        return False

    opened = _open_search()
    if not opened:
        # Если внутренний share sheet не закрылся сам, первый back закрывает
        # только его. Если он уже закрыт — возвращаемся из поста в профиль.
        d.press("back")
        human_pause(android_cfg, scale=0.25)
        opened = _open_search()
    if not opened:
        return None
    human_pause(android_cfg, scale=0.35)
    try:
        field = d(className="android.widget.EditText")
        if not field.exists(timeout=1.2):
            return None
        try:
            field.click()
        except Exception:
            pass
        d.shell("input keyevent 279")  # KEYCODE_PASTE; ничего не отправляет
        time.sleep(0.5)
        values: list[str] = []
        for getter in ("get_text",):
            try:
                value = getattr(field, getter)()
                if value:
                    values.append(str(value))
            except Exception:
                pass
        try:
            values.append(html.unescape(d.dump_hierarchy()))
        except Exception:
            pass
        for value in values:
            url = _first_tiktok_url(value)
            if url:
                return url
        return None
    finally:
        d.press("back")
        time.sleep(0.25)


def _tiktok_go_home(d, android_cfg: dict[str, Any]) -> None:
    """После публикации вернуться на вкладку «Главная»."""
    ensure_unlocked(d)
    package = (android_cfg.get("packages") or {}).get(
        "tiktok", "com.ss.android.ugc.trill"
    )
    current = (d.app_current() or {}).get("package") or ""
    if current != package:
        d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        human_pause(android_cfg, scale=0.9)
    for _ in range(3):
        if d(description="Создать").exists(timeout=0.4) or d(description="Create").exists(
            timeout=0.2
        ):
            break
        d.press("back")
        time.sleep(0.35)
    for label in ("Главная", "Home"):
        if click_text_or_desc(d, label, timeout=1.5):
            human_pause(android_cfg, scale=0.55)
            return
    w, h = d.window_size()
    d.click(int(w * 0.1), int(h * 0.94))
    human_pause(android_cfg, scale=0.55)


def _tiktok_media_long_press_center(d, *, channel: str) -> tuple[int, int]:
    """Центр видео/фото на экране открытого поста для long-press."""
    w, h = d.window_size()
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(d.dump_hierarchy() or "")
        best: tuple[int, int, int] | None = None
        for node in root.iter("node"):
            cls = (node.attrib.get("class") or "").lower()
            if not any(
                token in cls
                for token in (
                    "imageview",
                    "textureview",
                    "surfaceview",
                    "viewpager",
                    "framelayout",
                )
            ):
                continue
            parsed = _parse_bounds(node.attrib.get("bounds", ""))
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area < int(w * h * 0.12):
                continue
            if y1 > int(h * 0.72):
                continue
            if best is None or area > best[0]:
                best = (area, (x1 + x2) // 2, (y1 + y2) // 2)
        if best:
            return best[1], best[2]
    except Exception:
        pass
    # fallback: центр медиа-области (видео или карусель)
    y_ratio = 0.45 if channel == "tiktok_carousel" else 0.42
    return w // 2, int(h * y_ratio)


def _tiktok_scroll_profile_to_top(d, android_cfg: dict[str, Any], *, passes: int = 6) -> None:
    """Прокрутить сетку публикаций в самый верх (новые посты сверху)."""
    w, h = d.window_size()
    for _ in range(passes):
        d.swipe(w // 2, int(h * 0.2), w // 2, int(h * 0.82), 0.14)
        time.sleep(0.2)
    human_pause(android_cfg, scale=0.35)


def _tiktok_ensure_posts_grid(d, android_cfg: dict[str, Any]) -> None:
    """Вкладка сетки публикаций (не избранное / не лайки)."""
    for label in ("Публикации", "Posts", "Видео", "Videos"):
        if click_text_or_desc(d, label, timeout=0.9):
            human_pause(android_cfg, scale=0.45)
            return


def _tiktok_collect_grid_tiles(
    d,
) -> list[tuple[int, int, int, int, int, int]]:
    """Кликабельные тайлы сетки: (cx, cy, x1, y1, x2, y2)."""
    w, h = d.window_size()
    min_side = int(w * 0.26)
    max_side = int(w * 0.39)
    min_y = int(h * 0.30)
    tiles: list[tuple[int, int, int, int, int, int]] = []
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(d.dump_hierarchy() or "")
        for node in root.iter("node"):
            clickable = node.attrib.get("clickable", "false") == "true"
            desc = (node.attrib.get("content-desc") or "").strip()
            if not clickable and not desc:
                continue
            parsed = _parse_bounds(node.attrib.get("bounds", ""))
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            tw, th = x2 - x1, y2 - y1
            if y1 < min_y or y2 > int(h * 0.96):
                continue
            if not (min_side <= tw <= max_side and min_side <= th <= max_side):
                continue
            if abs(tw - th) > max(tw, th) * 0.28:
                continue
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            tiles.append((cx, cy, x1, y1, x2, y2))
    except Exception:
        return []
    # дедуп по почти совпадающим bounds
    deduped: list[tuple[int, int, int, int, int, int]] = []
    for tile in sorted(tiles, key=lambda item: (item[4], item[3])):
        if any(
            abs(tile[3] - kept[3]) < 8
            and abs(tile[5] - kept[5]) < 8
            and abs(tile[2] - kept[2]) < 8
            for kept in deduped
        ):
            continue
        deduped.append(tile)
    return deduped


def _tiktok_top_row_tiles(
    d,
) -> list[tuple[int, int, int, int, int, int]]:
    """Верхняя строка сетки профиля слева направо."""
    w, h = d.window_size()
    tiles = _tiktok_collect_grid_tiles(d)
    if not tiles:
        return []
    tiles.sort(key=lambda item: (item[4], item[3]))
    top_y = tiles[0][4]
    row_eps = max(int(h * 0.07), 36)
    return sorted(
        [tile for tile in tiles if abs(tile[4] - top_y) <= row_eps],
        key=lambda item: item[3],
    )


def _tiktok_first_profile_post_center(d) -> tuple[int, int]:
    """Самый новый пост: верхняя строка сетки, крайний левый тайл."""
    row = _tiktok_top_row_tiles(d)
    if row:
        return row[0][0], row[0][1]
    w, h = d.window_size()
    return w // 6, int(h * 0.52)


def _tiktok_chain_icon_center(image) -> tuple[int, int] | None:
    """Найти синий круг со значком цепи в нижнем long-press меню."""
    try:
        rgb = image.convert("RGB")
        width, height = rgb.size
        pixels = rgb.load()
    except Exception:
        return None

    y_start = int(height * 0.72)
    y_end = int(height * 0.95)
    seen: set[tuple[int, int]] = set()
    components: list[tuple[int, int, int, int, int]] = []

    def is_link_blue(x: int, y: int) -> bool:
        red, green, blue = pixels[x, y]
        return (
            blue >= 210
            and red <= 105
            and green <= 190
            and blue - red >= 100
            and blue - green >= 45
        )

    for y in range(y_start, y_end):
        for x in range(width):
            if (x, y) in seen or not is_link_blue(x, y):
                continue
            stack = [(x, y)]
            seen.add((x, y))
            count = 0
            left = right = x
            top = bottom = y
            while stack:
                px, py = stack.pop()
                count += 1
                left = min(left, px)
                right = max(right, px)
                top = min(top, py)
                bottom = max(bottom, py)
                for nx, ny in (
                    (px - 1, py),
                    (px + 1, py),
                    (px, py - 1),
                    (px, py + 1),
                ):
                    if (
                        0 <= nx < width
                        and y_start <= ny < y_end
                        and (nx, ny) not in seen
                        and is_link_blue(nx, ny)
                    ):
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            box_width = right - left + 1
            box_height = bottom - top + 1
            if (
                count >= max(120, int(width * height * 0.00015))
                and width * 0.045 <= box_width <= width * 0.16
                and width * 0.045 <= box_height <= width * 0.16
                and 0.65 <= box_width / max(1, box_height) <= 1.35
            ):
                components.append((left, top, right, bottom, count))

    if not components:
        return None
    # В long-press меню TikTok системная кнопка «Ссылка» стоит первой
    # перед динамическими приложениями (Messenger/Facebook/Email).
    left, top, right, bottom, _ = min(components, key=lambda item: item[0])
    return (left + right) // 2, (top + bottom) // 2


def _tiktok_copy_link_via_long_press(
    d,
    android_cfg: dict[str, Any],
    *,
    channel: str = "tiktok",
) -> str | None:
    """Удержать видео/фото 4 с → нажать синий значок цепи."""
    _clear_clipboard(d)
    photo_x, photo_y = _tiktok_media_long_press_center(d, channel=channel)
    d.shell(
        f"input swipe {photo_x} {photo_y} {photo_x} {photo_y} {_TIKTOK_LONG_PRESS_MS}"
    )
    human_pause(android_cfg, scale=0.5)
    center = _tiktok_chain_icon_center(d.screenshot())
    if center is not None:
        d.click(*center)
        time.sleep(0.8)
    else:
        for label in ("Ссылка", "Link", "Копировать ссылку", "Copy link"):
            if click_text_or_desc(d, label, timeout=0.7):
                time.sleep(0.6)
                break
    url = _first_tiktok_url(_read_clipboard_robust(d))
    if url:
        return url
    return _tiktok_url_from_sources(d, channel=channel)


def _tiktok_url_from_recent_log(
    d,
    *,
    channel: str,
    username: str,
    max_age_seconds: float,
) -> str | None:
    """Взять самый свежий ID открытого поста из событий TikTok."""
    try:
        result = d.shell("logcat -d -t 2500 -v brief")
        output = str(getattr(result, "output", result) or "")
    except Exception:
        return None
    kind = "photo" if channel == "tiktok_carousel" else "video"
    clean_username = (username or "_").strip().lstrip("@") or "_"
    candidates: list[tuple[int, str]] = []
    for match in _TIKTOK_LOG_UNIQUE_ID_RE.finditer(output):
        post_id = int(match.group("post_id"))
        url = f"https://www.tiktok.com/@{clean_username}/{kind}/{post_id}"
        if _tiktok_url_is_fresh_target(
            url,
            channel,
            max_age_seconds=max_age_seconds,
        ):
            candidates.append((post_id >> 32, url))
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def _tiktok_open_profile_grid(d, android_cfg: dict[str, Any]) -> None:
    ensure_unlocked(d)
    package = (android_cfg.get("packages") or {}).get(
        "tiktok", "com.ss.android.ugc.trill"
    )
    current = (d.app_current() or {}).get("package") or ""
    if current != package:
        d.app_start(package)
        human_pause(android_cfg, scale=0.9)
    if not (
        click_text_or_desc(d, "Профиль", timeout=2.5)
        or click_text_or_desc(d, "Profile", timeout=0.8)
    ):
        w, h = d.window_size()
        d.click(int(w * 0.91), int(h * 0.94))
    human_pause(android_cfg, scale=0.75)
    _tiktok_ensure_posts_grid(d, android_cfg)
    _tiktok_scroll_profile_to_top(d, android_cfg)


def _tiktok_copy_from_open_post(
    d,
    android_cfg: dict[str, Any],
    *,
    channel: str,
) -> str | None:
    _clear_clipboard(d)
    opened = False
    for label in (
        "Поделиться публикацией",
        "Поделиться видео",
        "Поделиться",
        "Share post",
        "Share video",
        "Share",
        "Отправить",
    ):
        if click_text_or_desc(d, label, timeout=0.8):
            opened = True
            break
    if not opened:
        for label in ("Поделиться", "Share"):
            selector = d(descriptionContains=label)
            if selector.exists(timeout=0.8):
                selector.click()
                opened = True
                break
    if not opened:
        return None
    human_pause(android_cfg, scale=0.5)

    # Иногда canonical URL уже присутствует в accessibility дерева share sheet.
    direct = _tiktok_url_from_sources(d, channel=channel)
    copied = False
    for label in ("Ссылка", "Link", "Копировать ссылку", "Copy link"):
        if click_text_or_desc(d, label, timeout=0.8):
            copied = True
            break
    if not copied:
        copied = _tap_copy_link(d, android_cfg)
    if copied:
        time.sleep(0.6)
    url = _tiktok_url_from_sources(d, channel=channel) or direct

    # Share sheet после «Ссылка» может закрыться сам; back нужен только если он остался.
    if (
        d(text="Отмена").exists(timeout=0.25)
        or d(text="Ссылка").exists(timeout=0.2)
        or d(text="Cancel").exists(timeout=0.2)
    ):
        d.press("back")
        time.sleep(0.25)
    if not url and copied:
        url = _tiktok_paste_probe(d, android_cfg)
    return url


def _tiktok_verify_clipboard_url(
    candidate: str | None,
    *,
    channel: str,
    max_age: float,
    resolve_timeout: float,
) -> str | None:
    if not candidate:
        return None
    if _is_tiktok_short_url(candidate):
        candidate = _resolve_tiktok_short_url(
            candidate,
            timeout=resolve_timeout,
        )
    if candidate and _tiktok_url_is_fresh_target(
        candidate,
        channel,
        max_age_seconds=max_age,
    ):
        return _canonical_tiktok_url(candidate)
    return None


def _capture_tiktok_url(
    d,
    android_cfg: dict[str, Any],
    *,
    channel: str,
) -> str | None:
    """Главная → профиль → вверх → левый верхний пост → long-press 4 с → цепь."""
    cfg = android_cfg.get("tiktok") or {}
    max_age = float(cfg.get("post_url_max_age_seconds") or 1800.0)
    resolve_timeout = float(cfg.get("post_url_resolve_timeout_seconds") or 8.0)
    username = str(cfg.get("profile_username") or "_")
    _tiktok_go_home(d, android_cfg)
    _tiktok_open_profile_grid(d, android_cfg)
    row = _tiktok_top_row_tiles(d)
    if not row:
        cx, cy = _tiktok_first_profile_post_center(d)
        row = [(cx, cy, 0, 0, 0, 0)]

    for index, tile in enumerate(row[:4]):
        if index:
            d.press("back")
            time.sleep(0.5)
            _tiktok_open_profile_grid(d, android_cfg)
        d.click(tile[0], tile[1])
        human_pause(android_cfg, scale=0.7)
        candidate = _tiktok_copy_link_via_long_press(
            d, android_cfg, channel=channel
        )
        verified = _tiktok_verify_clipboard_url(
            candidate,
            channel=channel,
            max_age=max_age,
            resolve_timeout=resolve_timeout,
        )
        if verified:
            return verified

    log_url = _tiktok_url_from_recent_log(
        d,
        channel=channel,
        username=username,
        max_age_seconds=max_age,
    )
    if log_url:
        return log_url
    return None


def capture_post_url(
    d,
    channel: str,
    android_cfg: dict[str, Any],
    *,
    wait_seconds: float = 12.0,
) -> tuple[str | None, dict[str, str]]:
    """
    После публикации ждём появления поста, открываем его и копируем ссылку.
    Возвращает (primary_url, extra_urls) — extra_urls для threads и т.п.
    """
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    ensure_unlocked(d)
    extra: dict[str, str] = {}
    patterns = _patterns_for_channel(channel)
    if not patterns:
        return None, extra

    try:
        if channel in ("tiktok", "tiktok_carousel"):
            url = _capture_tiktok_url(d, android_cfg, channel=channel)
            return url, extra

        if channel == "youtube_shorts":
            url = _capture_youtube_shorts_url(d, android_cfg)
            return url, extra

        if channel in ("instagram_reel", "instagram_carousel"):
            url = None
            url = _capture_instagram_url(d, android_cfg, channel=channel)
            if channel == "instagram_reel":
                threads = _try_threads_link(d, android_cfg)
                if threads:
                    extra["post_url_threads"] = threads
            return url, extra

        if channel == "linkedin":
            url = _capture_linkedin_url(d, android_cfg)
            return url, extra

        if channel == "twitter":
            url = _capture_twitter_url(d, android_cfg)
            return url, extra

        if channel == "fb_marketplace":
            # ссылку на объявление Marketplace не копируем
            return None, extra

        _open_profile_latest(d, channel, android_cfg)
        url = _open_share_and_copy(d, android_cfg, url_patterns=patterns)
        return url, extra
    except Exception:
        return None, extra


def _leave_threads_challenge(d, android_cfg: dict[str, Any]) -> None:
    """Пройти human-check («Продолжить») и вернуться в Instagram для захвата URL."""
    ig = (android_cfg.get("packages") or {}).get("instagram", "com.instagram.android")
    if pass_threads_human_check(d, timeout=1.0):
        human_pause(android_cfg, scale=0.5)
    cur = (d.app_current() or {}).get("package") or ""
    if cur != ig:
        d.shell(f"monkey -p {ig} -c android.intent.category.LAUNCHER 1")
        human_pause(android_cfg, scale=0.8)


def _parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(map(int, m.groups()))  # type: ignore[return-value]


def _instagram_list_profile_thumbs(
    d, *, min_y_ratio: float = 0.52, max_y_ratio: float = 0.95
) -> list[tuple[int, int, int, int]]:
    """Все thumbnail постов в сетке профиля: (y1, x1, cx, cy), сверху-слева."""
    from xml.etree import ElementTree as ET

    w, h = d.window_size()
    y_min, y_max = int(h * min_y_ratio), int(h * max_y_ratio)
    xml = d.dump_hierarchy()
    thumbs: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()

    skip_desc = (
        "вкладк",
        "профессиональн",
        "панель",
        "редактировать",
        "подписчик",
        "подписк",
        "настройк",
        "закрепл",
        "highlights",
        "актуальн",
        "поделиться профилем",
        "контакт",
    )

    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        desc_l = (a.get("content-desc") or "").lower()
        rid = a.get("resource-id") or ""
        if any(s in desc_l for s in skip_desc):
            continue
        if desc_l.strip() in ("reels", "сетка", "grid", "отметки", "tagged"):
            continue
        if "tab" in rid and "thumbnail" not in rid:
            continue
        if "bloks_container" in rid or "button_container" in rid:
            continue

        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        bw, bh = x2 - x1, y2 - y1
        # сетка 3×N: ячейка ~1/3 ширины; баннеры insights — на всю ширину
        if y1 < y_min or y1 > y_max or bw < 70 or bh < 70:
            continue
        if bw > int(w * 0.45) or bh > int(h * 0.35):
            continue

        is_thumb = rid.endswith(
            (
                "gallery_grid_item_thumbnail",
                "image_button",
                "media_set_row_thumbnail",
                "row_feed_photo_imageview",
            )
        ) or (
            a.get("clickable") == "true"
            and (
                "строк" in desc_l
                or "столбц" in desc_l
                or "фото пользователя" in desc_l
                or "photo by" in desc_l
                or "reel" in desc_l
                or "видео" in desc_l
                or "просмотр" in desc_l
                or "views" in desc_l
            )
        )
        if not is_thumb:
            continue

        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        key = (cx // 40, cy // 40)
        if key in seen:
            continue
        seen.add(key)
        thumbs.append((y1, x1, cx, cy))

    thumbs.sort()
    return thumbs


def _instagram_open_latest_post(d, package: str) -> bool:
    """Instagram → профиль → первый незакреплённый пост."""
    ensure_unlocked(d)
    cur = (d.app_current() or {}).get("package") or ""
    if cur != package:
        d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        time.sleep(1.5)

    w, h = d.window_size()
    if not (
        click_text_or_desc(d, "Профиль", timeout=2)
        or click_text_or_desc(d, "Profile", timeout=1)
    ):
        d.click(int(w * 0.9), int(h * 0.93))
        time.sleep(0.8)

    thumbs = _instagram_list_profile_thumbs(d, min_y_ratio=0.40)
    if not thumbs:
        return False
    _, __, cx, cy = thumbs[0]
    d.click(cx, cy)
    time.sleep(1.5)
    return True


def _instagram_post_is_fresh(d) -> bool:
    """
    Автопроверка принимает только явную метку секунд/минут.

    Если Instagram не отдал доступную метку времени, ссылка остаётся
    неподтверждённой: старый или закреплённый пост нельзя считать новым.
    """
    from xml.etree import ElementTree as ET

    try:
        root = ET.fromstring(d.dump_hierarchy())
    except Exception:
        return False
    for node in root.iter("node"):
        attrs = node.attrib
        for key in ("text", "content-desc"):
            value = (attrs.get(key) or "").strip()
            if _instagram_age_text_is_fresh(value):
                return True
    return False


def _instagram_age_text_is_fresh(value: str) -> bool:
    """Строго: сейчас/секунды или не более 15 минут."""
    if _INSTAGRAM_IMMEDIATE_AGE_RE.fullmatch(value or ""):
        return True
    match = _INSTAGRAM_RELATIVE_AGE_RE.fullmatch(value or "")
    if not match:
        return False
    count = int(match.group("count"))
    unit = match.group("unit").lower()
    if unit.startswith(("с", "sec")):
        return count <= 900
    return count <= 15


def _instagram_url_matches_channel(url: str | None, channel: str) -> bool:
    if not url:
        return False
    value = url.lower()
    if channel == "instagram_reel":
        return "/reel/" in value
    if channel == "instagram_carousel":
        return "/p/" in value
    return False


def _instagram_tap_airplane(d, package: str) -> bool:
    """Самолётик «Поделиться» у открытого поста."""
    rid = f"{package}:id/row_feed_button_share"
    if d(resourceId=rid).exists(timeout=1.5):
        d(resourceId=rid).click()
        time.sleep(0.6)
        return True
    for label in ("Поделиться", "Share", "Отправить"):
        if click_text_or_desc(d, label, timeout=0.8):
            time.sleep(0.5)
            return True
    w, h = d.window_size()
    d.click(int(w * 0.91), int(h * 0.55))
    time.sleep(0.6)
    return (
        d(textContains="Копир").exists(timeout=0.5)
        or d(textContains="Copy").exists(timeout=0.3)
        or d(textContains="ссылк").exists(timeout=0.3)
    )


def _instagram_copy_from_open_post(
    d, android_cfg: dict[str, Any], patterns: tuple[str, ...]
) -> str | None:
    package = (android_cfg.get("packages") or {}).get(
        "instagram", "com.instagram.android"
    )
    if not _instagram_tap_airplane(d, package):
        return None
    if not _tap_copy_link(d, android_cfg):
        d.press("back")
        time.sleep(0.3)
        return None
    url = _first_url(_read_clipboard_robust(d), patterns)
    if d(text="Отмена").exists(timeout=0.3):
        d(text="Отмена").click()
    elif d(text="Cancel").exists(timeout=0.2):
        d(text="Cancel").click()
    else:
        d.press("back")
    time.sleep(0.25)
    return url


def _capture_instagram_url(
    d, android_cfg: dict[str, Any], *, channel: str
) -> str | None:
    """Профиль → свежий незакреплённый пост → ссылка нужного типа."""
    package = (android_cfg.get("packages") or {}).get(
        "instagram", "com.instagram.android"
    )
    if not _instagram_open_latest_post(d, package):
        return None
    if not _instagram_post_is_fresh(d):
        return None
    url = _instagram_copy_from_open_post(d, android_cfg, ("instagram.com",))
    return url if _instagram_url_matches_channel(url, channel) else None


def _yt_dismiss_nags(d) -> None:
    for label in ("Нет, спасибо", "No thanks", "Не сейчас", "Закрыть", "Close"):
        if d(text=label).exists(timeout=0.25):
            d(text=label).click()
            time.sleep(0.4)


def _yt_on_own_shorts_grid(d) -> bool:
    """Сетка своих Shorts: вкладка Shorts + фильтр «Новые» / черновики."""
    has_shorts = d(text="Shorts").exists(timeout=0.4)
    has_grid_hint = (
        d(text="Новые").exists(timeout=0.25)
        or d(text="Черновики").exists(timeout=0.2)
        or d(textContains="видео").exists(timeout=0.2)
    )
    return bool(has_shorts and has_grid_hint)


def _yt_open_own_shorts_grid(d, android_cfg: dict[str, Any]) -> None:
    """Без перезапуска приложения: Вы → канал → Shorts."""
    _yt_dismiss_nags(d)
    if _yt_on_own_shorts_grid(d):
        return

    if not (
        click_text_or_desc(d, "Вы", timeout=2.5)
        or click_text_or_desc(d, "You", timeout=0.8)
    ):
        w, h = d.window_size()
        d.click(int(w * 0.9), int(h * 0.93))
        human_pause(android_cfg, scale=0.7)
    _yt_dismiss_nags(d)

    opened = False
    for label in ("Перейти на канал", "Ваши видео", "Ваш канал", "Your channel"):
        if click_text_or_desc(d, label, timeout=1.2):
            opened = True
            human_pause(android_cfg, scale=0.8)
            break
    if not opened:
        # уже на канале (после Загрузить часто остаёмся здесь)
        human_pause(android_cfg, scale=0.3)

    if d(text="Shorts").exists(timeout=1.5):
        try:
            d(text="Shorts")[0].click()
        except Exception:
            click_text_or_desc(d, "Shorts", timeout=0.8)
        human_pause(android_cfg, scale=0.7)

    # «Новые» — свежая публикация сверху-слева
    if d(text="Новые").exists(timeout=0.8):
        d(text="Новые").click()
        time.sleep(0.4)


def _yt_open_latest_short(d, android_cfg: dict[str, Any]) -> None:
    """Открыть самый новый Short в сетке (пропуск «Черновики»)."""
    from xml.etree import ElementTree as ET

    candidates: list[tuple[int, int, int, int]] = []
    for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
        a = node.attrib
        if a.get("clickable") != "true":
            continue
        desc = (a.get("content-desc") or "").strip()
        text = (a.get("text") or "").strip()
        if "Черновик" in desc or "Черновик" in text or "Draft" in desc:
            continue
        looks_short = (
            "Воспроизвести короткое видео" in desc
            or "Play short" in desc
            or ("просмотр" in desc.lower() and "видео" in desc.lower())
        )
        m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds") or "")
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        w, h = x2 - x1, y2 - y1
        if y1 < 280 or h < 120 or w < 120:
            continue
        if looks_short or (w < 420 and h > 180 and y1 > 420):
            candidates.append((y1, x1, (x1 + x2) // 2, (y1 + y2) // 2))
    candidates.sort()
    if candidates:
        _, __, cx, cy = candidates[0]
        d.click(cx, cy)
        human_pause(android_cfg, scale=1.0)
        return
    w, h = d.window_size()
    # верхний-левый тайл после «Черновики» (вторая ячейка первого ряда)
    d.click(int(w * 0.55), int(h * 0.58))
    human_pause(android_cfg, scale=1.0)


def _yt_tap_overflow(d, android_cfg: dict[str, Any]) -> bool:
    """Троеточие на открытом Short / карточке публикации."""
    labels = (
        "Ещё",
        "Действия",
        "Ещё действия",
        "Другие действия",
        "More options",
        "More actions",
        "More",
        "Overflow",
    )
    for label in labels:
        if click_text_or_desc(d, label, timeout=0.7):
            human_pause(android_cfg, scale=0.55)
            return True
        if d(descriptionContains=label).exists(timeout=0.25):
            d(descriptionContains=label).click()
            human_pause(android_cfg, scale=0.55)
            return True
    # правый верх / зона меню плеера
    w, h = d.window_size()
    for x_r, y_r in ((0.93, 0.08), (0.93, 0.12), (0.88, 0.10)):
        d.click(int(w * x_r), int(h * y_r))
        time.sleep(0.7)
        if d(text="Поделиться").exists(timeout=0.5) or d(text="Share").exists(
            timeout=0.3
        ):
            return True
        # если меню не открылось — назад и пробуем другую точку
        if not (
            d(textContains="удал").exists(timeout=0.2)
            or d(textContains="Редакт").exists(timeout=0.2)
            or d(textContains="плейлист").exists(timeout=0.2)
        ):
            continue
        return True
    return False


def _yt_tap_share_second_from_top(d, android_cfg: dict[str, Any]) -> bool:
    """В меню троеточия «Поделиться» — вторая кнопка сверху."""
    from xml.etree import ElementTree as ET

    # явное имя, если a11y есть
    if click_text_or_desc(d, "Поделиться", timeout=0.8) or click_text_or_desc(
        d, "Share", timeout=0.4
    ):
        human_pause(android_cfg, scale=0.6)
        return True

    rows: list[tuple[int, int, int, int]] = []
    for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
        a = node.attrib
        if a.get("clickable") != "true":
            continue
        m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds") or "")
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        # bottom sheet / список действий
        if y1 < 350 or y2 > 1500:
            continue
        if (x2 - x1) < 280 or not (70 <= (y2 - y1) <= 200):
            continue
        desc = (a.get("content-desc") or "").strip().lower()
        text = (a.get("text") or "").strip().lower()
        if any(x in f"{text} {desc}" for x in ("назад", "back", "закрыть", "close")):
            continue
        rows.append((y1, y2, x1, x2))
    rows.sort()
    uniq: list[tuple[int, int, int, int]] = []
    seen: set[int] = set()
    for r in rows:
        k = r[0] // 12
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    if len(uniq) < 2:
        return False
    y1, y2, x1, x2 = uniq[1]  # вторая сверху
    d.click((x1 + x2) // 2, (y1 + y2) // 2)
    human_pause(android_cfg, scale=0.65)
    return True


def _capture_youtube_shorts_url(d, android_cfg: dict[str, Any]) -> str | None:
    """
    Без выхода из YouTube: после ожидания → последняя публикация →
    троеточие → 2-й пункт («Поделиться») → Копировать ссылку.
    """
    patterns = ("youtube.com", "youtu.be")

    _yt_open_own_shorts_grid(d, android_cfg)
    _yt_open_latest_short(d, android_cfg)

    if not _yt_tap_overflow(d, android_cfg):
        # запасной путь: боковая «Поделиться» на плеере
        url = _open_share_and_copy(
            d,
            android_cfg,
            share_labels=(
                "Поделиться видео",
                "Поделиться",
                "Share",
                "Отправить",
            ),
            url_patterns=patterns,
        )
        return url

    if not _yt_tap_share_second_from_top(d, android_cfg):
        d.press("back")
        time.sleep(0.35)
        return _open_share_and_copy(
            d,
            android_cfg,
            share_labels=("Поделиться видео", "Поделиться", "Share"),
            url_patterns=patterns,
        )

    _clear_clipboard(d)
    if not _tap_copy_link(d, android_cfg):
        d.press("back")
        time.sleep(0.3)
        return None
    human_pause(android_cfg, scale=0.4)
    url = _first_url(_read_clipboard_robust(d), patterns)
    if d(text="Отмена").exists(timeout=0.3) or d(text="Cancel").exists(timeout=0.2):
        click_text_or_desc(d, "Отмена", timeout=0.3) or click_text_or_desc(
            d, "Cancel", timeout=0.2
        )
    else:
        d.press("back")
    time.sleep(0.25)
    return url


def _patterns_for_channel(channel: str) -> tuple[str, ...]:
    mapping = {
        "tiktok": ("tiktok.com", "vm.tiktok.com"),
        "tiktok_carousel": ("tiktok.com", "vm.tiktok.com"),
        "instagram_reel": ("instagram.com",),
        "instagram_carousel": ("instagram.com",),
        "youtube_shorts": ("youtube.com", "youtu.be"),
        "linkedin": ("linkedin.com",),
        "twitter": ("x.com", "twitter.com"),
        "fb_groups": ("facebook.com",),
    }
    return mapping.get(channel, ())


def _linkedin_copy_from_share_sheet(d, android_cfg: dict[str, Any]) -> str | None:
    patterns = ("linkedin.com",)
    _clear_clipboard(d)
    for label in (
        "Копировать ссылку на публикацию",
        "Копировать ссылку",
        "Copy link to post",
        "Copy link",
    ):
        if click_text_or_desc(d, label, timeout=0.8):
            time.sleep(0.4)
            return _first_url(_read_clipboard_robust(d), patterns)
    if _tap_copy_link(d, android_cfg):
        return _first_url(_read_clipboard_robust(d), patterns)
    xml = d.dump_hierarchy()
    for t in re.findall(r'text="(https?://[^"]+linkedin\.com[^"]+)"', xml):
        url = _first_url(t.replace("&amp;", "&"), patterns)
        if url:
            return url
    return None


def _tap_linkedin_airplane(d) -> bool:
    """Самолётик «Поделиться» у поста в ленте."""
    from xml.etree import ElementTree as ET

    # приоритет: явные a11y у иконки share/airplane
    for label in (
        "Поделиться",
        "Share",
        "Отправить",
        "Send",
        "Поделиться публикацией",
        "Share post",
    ):
        if d(description=label).exists(timeout=0.6):
            # берём первую в ленте (верхний пост)
            try:
                d(description=label)[0].click()
            except Exception:
                d(description=label).click()
            time.sleep(0.7)
            return True
        if d(descriptionContains=label).exists(timeout=0.35):
            d(descriptionContains=label).click()
            time.sleep(0.7)
            return True

    xml = d.dump_hierarchy()
    planes: list[tuple[int, tuple[int, int, int, int]]] = []
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        if a.get("clickable") != "true":
            continue
        desc = ((a.get("content-desc") or "") + " " + (a.get("text") or "")).lower()
        rid = (a.get("resource-id") or "").lower()
        hit = any(
            k in desc
            for k in ("поделиться", "share", "отправить", "send", "airplane", "самолёт", "самолет")
        ) or any(k in rid for k in ("share", "send", "repost"))
        if not hit:
            continue
        m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds") or "")
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        # иконка в ряду реакций — компактная, не на всю ширину
        if y1 < 200 or (y2 - y1) > 220 or (x2 - x1) > 280:
            continue
        planes.append((y1, (x1, y1, x2, y2)))
    planes.sort(key=lambda x: x[0])
    if not planes:
        return False
    x1, y1, x2, y2 = planes[0][1]
    d.click((x1 + x2) // 2, (y1 + y2) // 2)
    time.sleep(0.7)
    return True


def _capture_linkedin_url(d, android_cfg: dict[str, Any]) -> str | None:
    """
    После +60с: лента → самолётик у свежего поста → Копировать ссылку.
    Fallback: профиль → дополнительные опции → Поделиться посредством.
    """
    patterns = ("linkedin.com",)
    package = (android_cfg.get("packages") or {}).get("linkedin", "com.linkedin.android")
    ensure_unlocked(d)

    # остаёмся в LI; если ушли — на Главную (пост после публикации сверху)
    cur = (d.app_current() or {}).get("package") or ""
    if cur != package:
        d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        time.sleep(1.0)
        ensure_unlocked(d)
    for label in ("Пропустить", "Skip", "Не сейчас", "Not now"):
        if d(text=label).exists(timeout=0.25):
            d(text=label).click()
            time.sleep(0.25)

    if d(description="Главная").exists(timeout=0.8) or d(descriptionContains="Главная").exists(
        timeout=0.4
    ):
        if d(description="Главная").exists():
            d(description="Главная").click()
        else:
            d(descriptionContains="Главная").click()
        time.sleep(0.6)
    elif d(text="Главная").exists(timeout=0.4):
        d(text="Главная").click()
        time.sleep(0.6)

    # лёгкий pull-to-refresh — пост должен появиться
    w, h = d.window_size()
    d.swipe(w // 2, int(h * 0.32), w // 2, int(h * 0.62), 0.2)
    time.sleep(1.0)

    # самолётик у поста
    if _tap_linkedin_airplane(d):
        # иногда сначала sheet LI «Поделиться посредством»
        if d(text="Поделиться посредством").exists(timeout=0.8):
            d(text="Поделиться посредством").click()
            time.sleep(0.7)
        url = _linkedin_copy_from_share_sheet(d, android_cfg)
        if url:
            return url
        url = _open_share_and_copy(
            d,
            android_cfg,
            share_labels=("Поделиться", "Share", "Отправить"),
            url_patterns=patterns,
        )
        if url:
            return url

    # Fallback: меню → профиль → ⋯ → Поделиться посредством
    if d(descriptionContains="Кнопка меню").exists(timeout=1.2):
        d(descriptionContains="Кнопка меню").click()
        time.sleep(0.6)
    for label in ("См. свой профиль", "Посмотреть профиль", "View profile"):
        if click_text_or_desc(d, label, timeout=0.8):
            time.sleep(0.8)
            break
    for _ in range(5):
        if d(textContains="Вилла").exists(timeout=0.3) or d(text="Публикации").exists(timeout=0.25):
            break
        d.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.35), 0.2)
        time.sleep(0.4)
    if d(textContains="Вилла в Аренду").exists(timeout=0.6):
        d(textContains="Вилла в Аренду").click()
        time.sleep(0.7)
    if d(description="См. дополнительные опции").exists(timeout=1.0):
        d(description="См. дополнительные опции").click()
        time.sleep(0.5)
    if d(text="Поделиться посредством").exists(timeout=1.0):
        d(text="Поделиться посредством").click()
        time.sleep(0.7)
        return _linkedin_copy_from_share_sheet(d, android_cfg)
    return None


def _capture_twitter_url(d, android_cfg: dict[str, Any]) -> str | None:
    """
    Панель слева сверху → Профиль → вкладка Посты →
    чуть вниз до ряда под последним постом →
    правая кнопка «Поделиться» → «Копировать ссылку».
    """
    from xml.etree import ElementTree as ET

    patterns = ("x.com", "twitter.com")
    package = (android_cfg.get("packages") or {}).get("twitter", "com.twitter.android")
    ensure_unlocked(d)
    # всегда с чистого запуска — иначе остаёмся на профиле/sheet и ломаем навигацию
    try:
        d.app_stop(package)
        time.sleep(0.35)
        d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        time.sleep(1.2)
    except Exception:
        pass
    ensure_unlocked(d)
    for label in ("Пропустить", "Skip", "Not now", "Не сейчас"):
        if d(text=label).exists(timeout=0.25):
            d(text=label).click()
            time.sleep(0.25)

    if d(description="Главная").exists(timeout=0.7):
        d(description="Главная").click()
        time.sleep(0.4)

    if d(description="Показать панель навигации").exists(timeout=2):
        d(description="Показать панель навигации").click()
        time.sleep(0.8)
    else:
        w, h = d.window_size()
        d.click(int(w * 0.08), int(h * 0.08))
        time.sleep(0.8)

    if not (
        click_text_or_desc(d, "Профиль", timeout=2.0)
        or click_text_or_desc(d, "Profile", timeout=0.7)
    ):
        return None
    time.sleep(1.2)

    if d(text="Посты").exists(timeout=0.8) or d(description="Посты").exists(timeout=0.4):
        click_text_or_desc(d, "Посты", timeout=0.5)
        time.sleep(0.45)

    w, h = d.window_size()

    def _post_share_centers() -> list[tuple[int, int]]:
        """Центры иконок «Поделиться» в ряду под постом (не кнопка профиля)."""
        xml = d.dump_hierarchy()
        found: list[tuple[int, int]] = []
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            desc = (a.get("content-desc") or "").strip()
            if desc not in ("Поделиться", "Share"):
                continue
            m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds") or "")
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            bw, bh = x2 - x1, y2 - y1
            # иконка в action-row (~30px); кнопка «Поделиться» профиля — шире
            if bw > 120 or bh > 120 or y1 < 280 or y1 > int(h * 0.92):
                continue
            found.append(((x1 + x2) // 2, (y1 + y2) // 2))
        found.sort(key=lambda p: p[1])
        return found

    tapped = False
    for _ in range(10):
        centers = _post_share_centers()
        if centers:
            d.click(*centers[0])  # первый/верхний пост
            time.sleep(0.85)
            tapped = True
            break
        # вниз — ряд кнопок под медиа первого поста
        d.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.38), 0.2)
        time.sleep(0.55)

    if not tapped:
        # запасной: правый край ряда кликабельных под постом
        xml = d.dump_hierarchy()
        cells: list[tuple[int, int, int, int]] = []
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            if a.get("clickable") != "true":
                continue
            m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds") or "")
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            bw, bh = x2 - x1, y2 - y1
            if y1 < 400 or bw > 160 or bh > 120 or bw < 50:
                continue
            cells.append((y1, x1, x2, y2))
        bands: dict[int, list[tuple[int, int, int, int]]] = {}
        for c in cells:
            bands.setdefault(c[0] // 35, []).append(c)
        band = next(
            (
                b
                for b in sorted(bands.values(), key=lambda x: (-len(x), min(i[0] for i in x)))
                if len(b) >= 4
            ),
            None,
        )
        if not band:
            return None
        right = max(band, key=lambda c: c[1])
        d.click((right[1] + right[2]) // 2, (right[0] + right[3]) // 2)
        time.sleep(0.85)

    if not (
        d(text="Копировать ссылку").exists(timeout=1.5)
        or d(text="Copy link").exists(timeout=0.4)
        or d(textContains="Копировать").exists(timeout=0.4)
    ):
        return None

    _clear_clipboard(d)
    for label in ("Копировать ссылку", "Copy link", "Скопировать ссылку"):
        if click_text_or_desc(d, label, timeout=1.0):
            time.sleep(0.45)
            url = _first_url(_read_clipboard_robust(d), patterns)
            if url:
                return url
    if _tap_copy_link(d, android_cfg):
        return _first_url(_read_clipboard_robust(d), patterns)
    return None


def _open_profile_latest(d, channel: str, android_cfg: dict[str, Any]) -> None:
    if channel in ("tiktok", "tiktok_carousel"):
        if click_text_or_desc(d, "Профиль", timeout=3) or click_text_or_desc(d, "Profile", timeout=1):
            human_pause(android_cfg, scale=1.0)
        w, h = d.window_size()
        d.click(w // 2, int(h * 0.55))
        human_pause(android_cfg, scale=0.8)
        return

    if channel in ("instagram_reel", "instagram_carousel"):
        if click_text_or_desc(d, "Профиль", timeout=3) or click_text_or_desc(d, "Profile", timeout=1):
            human_pause(android_cfg, scale=1.0)
        w, h = d.window_size()
        d.click(int(w * 0.12), int(h * 0.42))
        human_pause(android_cfg, scale=0.8)
        return

    if channel == "youtube_shorts":
        if click_text_or_desc(d, "Вы", timeout=3) or click_text_or_desc(d, "You", timeout=1):
            human_pause(android_cfg, scale=1.0)
        w, h = d.window_size()
        d.click(int(w * 0.15), int(h * 0.35))
        human_pause(android_cfg, scale=0.8)
        return

    if channel == "linkedin":
        if click_text_or_desc(d, "Профиль", timeout=3):
            human_pause(android_cfg, scale=1.0)
        w, h = d.window_size()
        d.click(w // 2, int(h * 0.45))
        human_pause(android_cfg, scale=0.8)
        return

    if channel == "twitter":
        if click_text_or_desc(d, "Профиль", timeout=3) or click_text_or_desc(d, "Profile", timeout=1):
            human_pause(android_cfg, scale=1.0)
        w, h = d.window_size()
        d.click(w // 2, int(h * 0.35))
        human_pause(android_cfg, scale=0.8)
        return

    if channel.startswith("fb_"):
        if click_text_or_desc(d, "Меню", timeout=2) or click_text_or_desc(d, "Menu", timeout=1):
            human_pause(android_cfg, scale=0.6)
        if click_text_or_desc(d, "Профиль", timeout=2) or click_text_or_desc(d, "Profile", timeout=1):
            human_pause(android_cfg, scale=0.8)


def _try_threads_link(d, android_cfg: dict[str, Any]) -> str | None:
    """Попытка открыть Threads и скопировать ссылку на последний пост."""
    pkg = "com.instagram.barcelona"
    try:
        d.app_start(pkg)
        human_pause(android_cfg, scale=1.2)
        # один тап «Продолжить» на human-check
        pass_threads_human_check(d, timeout=2.0)
        human_pause(android_cfg, scale=0.6)
        w, h = d.window_size()
        d.click(int(w * 0.12), int(h * 0.42))
        human_pause(android_cfg, scale=0.8)
        return _open_share_and_copy(
            d,
            android_cfg,
            url_patterns=("threads.net", "threads.com"),
        )
    except Exception:
        return None
