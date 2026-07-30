from __future__ import annotations

import re
import time
from typing import Any
from xml.etree import ElementTree as ET

from ..android.ui import (
    click_text_or_desc,
    connect_device,
    dismiss_permissions,
    ensure_unlocked,
    human_pause,
)
from ..dotenv_util import package_root
from ..models import PublishJob
from ._post_url import attach_post_url
from .base import ChannelResult

PKG_DEFAULT = "com.google.android.youtube"
# YouTube picker: системный фильтр «Видео», не папка publisher_social.
ALBUM_DEFAULT = "Видео"
TITLE_MAX = 100
DESC_MAX = 5000


def _package(android_cfg: dict[str, Any]) -> str:
    packages = android_cfg.get("packages") or {}
    return packages.get("youtube") or PKG_DEFAULT


def _yt_cfg(publisher_cfg: dict[str, Any], android_cfg: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(publisher_cfg.get("youtube_shorts") or publisher_cfg.get("youtube") or {})
    cfg.update(android_cfg.get("youtube") or {})
    return cfg


def _parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _one_line(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _housing_word(housing_type: str | None) -> str:
    key = (housing_type or "").strip().lower()
    if any(x in key for x in ("вилл", "villa")):
        return "вилла"
    if any(x in key for x in ("квартир", "apartment", "condo", "кондо", "апартамент")):
        return "квартира"
    if any(x in key for x in ("таунхаус", "townhouse", "town house")):
        return "таунхаус"
    if any(x in key for x in ("дом", "house", "home")):
        return "дом"
    return "вилла" if not key else key


def _format_rooms_short(rooms: float | int | None) -> str:
    if rooms is None:
        return ""
    if float(rooms).is_integer():
        n = int(rooms)
    else:
        n = rooms
    return f"{n}BR"


def _build_shorts_title_from_listing(job: PublishJob, *, max_chars: int = TITLE_MAX) -> str:
    listing = job.listing
    kind = _housing_word(listing.housing_type)
    rooms = _format_rooms_short(listing.rooms)
    district = (listing.district or "").strip()
    parts = ["Аренда", kind]
    if rooms:
        parts.append(rooms)
    parts.append("Phuket")
    if district:
        parts.append(district)
    return _one_line(" ".join(parts), max_chars)


def _build_youtube_copy(
    job: PublishJob, *, max_title: int = TITLE_MAX, max_desc: int = DESC_MAX
) -> tuple[str, str]:
    yt_title = (job.title_youtube_shorts or "").strip()
    title = (
        _one_line(yt_title, max_title)
        if yt_title
        else _build_shorts_title_from_listing(job, max_chars=max_title)
    )
    desc = (job.caption_social or "").strip()
    if len(desc) > max_desc:
        desc = desc[: max_desc - 1].rstrip() + "…"
    return title, desc


def _dismiss_overlays(d) -> None:
    for _ in range(3):
        acted = False
        for label in ("Нет, спасибо", "No thanks", "Не сейчас", "Not now"):
            if d(text=label).exists(timeout=0.3):
                d(text=label).click()
                time.sleep(0.6)
                acted = True
                break
        if not acted:
            break


def _dismiss_media_permission(d) -> None:
    for label in (
        "Разрешить ко всем",
        "Allow all",
        "При использовании приложения",
        "Только в этот раз",
        "Разрешить",
        "Allow",
    ):
        if d(text=label).exists(timeout=0.8):
            d(text=label).click()
            time.sleep(0.8)
            return
    dismiss_permissions(d)


def _click_label(d, *labels: str, timeout: float = 3.0) -> bool:
    for label in labels:
        if click_text_or_desc(d, label, timeout=timeout):
            return True
        if d(textContains=label).exists(timeout=0.3):
            d(textContains=label).click()
            return True
        if d(descriptionContains=label).exists(timeout=0.3):
            d(descriptionContains=label).click()
            return True
    return False


def _hide_keyboard(d) -> None:
    try:
        d.hide_keyboard()
    except Exception:
        pass
    time.sleep(0.25)


def _type_into_edit(d, text: str) -> None:
    et = d(className="android.widget.EditText")
    if not et.exists(timeout=3):
        raise RuntimeError("Поле ввода не найдено")
    et.click()
    time.sleep(0.25)
    try:
        et.set_text(text)
    except Exception:
        d.send_keys(text)
    time.sleep(0.35)
    _hide_keyboard(d)


# ─── Flow steps ──────────────────────────────────────────────────────────────


def _open_youtube(d, package: str, android_cfg: dict[str, Any]) -> None:
    ensure_unlocked(d)
    d.press("home")
    time.sleep(0.4)
    d.app_stop(package)
    time.sleep(0.5)
    d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    human_pause(android_cfg, scale=1.0)
    _dismiss_overlays(d)
    for _ in range(12):
        _dismiss_overlays(d)
        if (
            d(description="Создание видео").exists(timeout=0.5)
            or d(description="Create").exists(timeout=0.3)
            or d(description="Shorts").exists(timeout=0.3)
        ):
            return
        time.sleep(0.5)
    raise RuntimeError("YouTube: кнопка «+» / Создание видео не найдена")


def step1_press_plus(d, android_cfg: dict[str, Any]) -> None:
    """1. Плюс → Shorts → «Добавить» (галерея)."""
    if d(description="Создание видео").exists(timeout=3):
        d(description="Создание видео").click()
    elif d(description="Create").exists(timeout=1):
        d(description="Create").click()
    elif not _click_label(d, "Создание видео", "Create", timeout=1):
        raise RuntimeError("Шаг 1: плюсик «Создание видео» не найден")
    human_pause(android_cfg, scale=0.8)
    _dismiss_media_permission(d)

    if d(text="Shorts").exists(timeout=1.5) or d(description="Shorts").exists(timeout=0.4):
        _click_label(d, "Shorts", timeout=1)
        time.sleep(0.5)

    opened = False
    for _ in range(8):
        _dismiss_media_permission(d)
        if d(description="Загрузить видео из галереи").exists(timeout=0.6):
            d(description="Загрузить видео из галереи").click()
            opened = True
            break
        if d(descriptionContains="галереи").exists(timeout=0.35):
            d(descriptionContains="галереи").click()
            opened = True
            break
        if d(text="Добавить").exists(timeout=0.5):
            d(text="Добавить").click()
            opened = True
            break
        if d(description="Добавить").exists(timeout=0.35):
            d(description="Добавить").click()
            opened = True
            break
        if d(descriptionContains="Добавить").exists(timeout=0.3):
            d(descriptionContains="Добавить").click()
            opened = True
            break
        time.sleep(0.3)

    if not opened:
        w, h = d.window_size()
        d.click(int(w * 0.12), int(h * 0.88))
        time.sleep(0.8)

    human_pause(android_cfg, scale=0.7)
    _dismiss_media_permission(d)


def _close_gallery_dropdown(d) -> None:
    """Закрыть выпадающее меню (Недавние/Фото/Видео), если оно перекрывает сетку."""
    if d(text="Недавние").exists(timeout=0.4) and d(text="Все альбомы").exists(
        timeout=0.3
    ):
        d.press("back")
        time.sleep(0.5)


def _open_youtube_album(d, album: str, android_cfg: dict[str, Any]) -> None:
    """В пикере предпочитаем «Видео»; если нет — оставляем текущую сетку и берём последнее."""
    _close_gallery_dropdown(d)
    video_aliases = ("Видео", "Videos", "Video")

    header = ""
    btn = d(resourceIdMatches=r".*:id/select_album_button")
    if btn.exists(timeout=1.2):
        try:
            header = btn.get_text() or ""
        except Exception:
            header = ""
        if any(a in header for a in video_aliases):
            return
        btn.click()
        time.sleep(0.55)

    for label in video_aliases:
        if d(text=label).exists(timeout=0.9):
            d(text=label).click()
            human_pause(android_cfg, scale=0.45)
            _close_gallery_dropdown(d)
            return
        if d(description=label).exists(timeout=0.35):
            d(description=label).click()
            human_pause(android_cfg, scale=0.45)
            _close_gallery_dropdown(d)
            return

    if d(text="Все альбомы").exists(timeout=0.8):
        d(text="Все альбомы").click()
        human_pause(android_cfg, scale=0.4)
        for label in video_aliases:
            if d(text=label).exists(timeout=1.2):
                d(text=label).click()
                human_pause(android_cfg, scale=0.5)
                _close_gallery_dropdown(d)
                return
            try:
                d(scrollable=True).scroll.to(text=label)
            except Exception:
                pass
            if d(text=label).exists(timeout=0.8):
                d(text=label).click()
                human_pause(android_cfg, scale=0.5)
                _close_gallery_dropdown(d)
                return
        d.press("back")
        time.sleep(0.4)

    _close_gallery_dropdown(d)


def step2_select_video(
    d,
    android_cfg: dict[str, Any],
    album: str = ALBUM_DEFAULT,
    *,
    object_id: str | None = None,
) -> None:
    """2. Фильтр «Видео» → последнее/самое новое видео (первая ячейка)."""
    _open_youtube_album(d, album or ALBUM_DEFAULT, android_cfg)
    _close_gallery_dropdown(d)

    # Кандидаты: (y, x, cx, cy). Берём верхний-левый — обычно самое новое.
    tiles: list[tuple[int, int, int, int]] = []
    for _ in range(2):
        tiles.clear()
        for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
            a = node.attrib
            rid = a.get("resource-id") or ""
            text = (a.get("text") or "").strip()
            desc = (a.get("content-desc") or "").lower()
            clickable = a.get("clickable") == "true"
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            w, h = x2 - x1, y2 - y1
            if y1 < 160 or w < 90 or h < 90:
                continue
            looks_video = bool(
                re.match(r"^\d+:\d{2}$", text)
                or "thumb" in rid
                or "thumbnail" in rid
                or ".mp4" in desc
                or "video" in desc
                or "видео" in desc
            )
            if not (looks_video or clickable):
                continue
            # Игнорируем широкие кнопки/баннеры
            if w > 420 and h < 140:
                continue
            tiles.append((y1, x1, (x1 + x2) // 2, (y1 + y2) // 2))
        if tiles:
            break
        _close_gallery_dropdown(d)
        time.sleep(0.35)

    if not tiles:
        w, h = d.window_size()
        d.click(w // 4, int(h * 0.32))
        human_pause(android_cfg, scale=0.6)
        return

    tiles.sort()  # сверху-слева = новее
    _, __, cx, cy = tiles[0]
    d.click(cx, cy)
    human_pause(android_cfg, scale=0.8)


def step3_press_gotovo(d, android_cfg: dict[str, Any]) -> None:
    """3. Жмём «Готово» (или уже на экране публикации — пропуск)."""
    ensure_unlocked(d)
    # Новые версии YouTube иногда сразу открывают форму Shorts без «Готово».
    if (
        d(text="Добавьте название").exists(timeout=0.8)
        or d(text="Загрузить").exists(timeout=0.3)
        or d(text="Добавьте информацию").exists(timeout=0.3)
        or d(text="Upload").exists(timeout=0.3)
        or d(resourceIdMatches=r".*:id/shorts_post_bottom_button").exists(timeout=0.3)
    ):
        return

    human_pause(android_cfg, scale=0.5)

    # Подтверждение выбора в галерее / триммере
    if not d(text="Готово").exists(timeout=1.0):
        if d(resourceIdMatches=r".*:id/multi_select_next_button").exists(timeout=0.8):
            d(resourceIdMatches=r".*:id/multi_select_next_button").click()
            human_pause(android_cfg, scale=0.6)
        elif d(resourceIdMatches=r".*:id/button_done").exists(timeout=0.5):
            d(resourceIdMatches=r".*:id/button_done").click()
            human_pause(android_cfg, scale=0.6)
        elif d(text="Далее").exists(timeout=0.5) and not d(text="Загрузить").exists(
            timeout=0.2
        ):
            d(text="Далее").click()
            human_pause(android_cfg, scale=0.6)

    done_labels = (
        "Готово",
        "Done",
        "ГОТОВО",
        "OK",
        "ОК",
    )
    for _ in range(16):
        for label in done_labels:
            if d(text=label).exists(timeout=0.4):
                d(text=label).click()
                human_pause(android_cfg, scale=0.8)
                return
            if d(description=label).exists(timeout=0.25):
                d(description=label).click()
                human_pause(android_cfg, scale=0.8)
                return
            if _click_label(d, label, timeout=0.3):
                human_pause(android_cfg, scale=0.8)
                return
        # Иконка галочки в toolbar (часто без текста)
        for desc in ("Готово", "Done", "Подтвердить", "Confirm"):
            if d(descriptionContains=desc).exists(timeout=0.25):
                d(descriptionContains=desc).click()
                human_pause(android_cfg, scale=0.8)
                return
        if d(resourceIdMatches=r".*:id/action_done").exists(timeout=0.25):
            d(resourceIdMatches=r".*:id/action_done").click()
            human_pause(android_cfg, scale=0.8)
            return
        for rid in (
            r".*:id/done_button",
            r".*:id/menu_done",
            r".*:id/checkmark",
            r".*:id/accept_button",
            r".*:id/next_button",
        ):
            if d(resourceIdMatches=rid).exists(timeout=0.2):
                d(resourceIdMatches=rid).click()
                human_pause(android_cfg, scale=0.8)
                return
        if d(text="Следующий").exists(timeout=0.25) or d(text="Next").exists(timeout=0.25):
            for label in ("Следующий", "Next"):
                if d(text=label).exists(timeout=0.2):
                    d(text=label).click()
                    human_pause(android_cfg, scale=0.6)
                    break
        if (
            d(text="Далее").exists(timeout=0.3)
            and (
                d(text="Загрузить").exists(timeout=0.2)
                or d(text="Добавьте название").exists(timeout=0.2)
            )
        ):
            return
        if d(text="Добавьте название").exists(timeout=0.2) or d(text="Загрузить").exists(
            timeout=0.2
        ):
            return
        time.sleep(0.35)

    # Samsung / Google Photos — галочка справа сверху
    try:
        w, h = d.window_size()
        d.click(int(w * 0.92), int(h * 0.08))
        human_pause(android_cfg, scale=0.8)
        if d(text="Добавьте название").exists(timeout=1.5) or d(text="Загрузить").exists(
            timeout=0.5
        ):
            return
    except Exception:
        pass

    try:
        dump_dir = package_root() / "data" / "logs"
        dump_dir.mkdir(parents=True, exist_ok=True)
        dump_path = dump_dir / "youtube_step3_fail.xml"
        dump_path.write_text(d.dump_hierarchy(), encoding="utf-8")
    except Exception:
        pass
    if "keyguard" in (d.dump_hierarchy() or "").lower():
        raise RuntimeError(
            "Шаг 3: телефон заблокирован — разблокируйте экран и повторите"
        )
    raise RuntimeError("Шаг 3: кнопка «Готово» не найдена")


def step4_press_dalee(d, android_cfg: dict[str, Any]) -> None:
    """4. Жмём «Далее» → экран публикации"""
    for _ in range(12):
        if d(text="Загрузить").exists(timeout=0.4) or d(text="Upload").exists(timeout=0.2):
            return
        if d(text="Добавьте название").exists(timeout=0.3):
            return
        if d(resourceIdMatches=r".*:id/shorts_post_bottom_button").exists(timeout=0.4):
            d(resourceIdMatches=r".*:id/shorts_post_bottom_button").click()
            human_pause(android_cfg, scale=0.8)
            continue
        if d(text="Далее").exists(timeout=0.4):
            d(text="Далее").click()
            human_pause(android_cfg, scale=0.8)
            continue
        if d(text="Next").exists(timeout=0.3):
            d(text="Next").click()
            human_pause(android_cfg, scale=0.8)
            continue
        time.sleep(0.35)
    if not (
        d(text="Загрузить").exists(timeout=2)
        or d(className="android.widget.EditText").exists(timeout=1)
    ):
        raise RuntimeError("Шаг 4: экран публикации не открылся после «Далее»")


def step5_set_title(d, title: str, android_cfg: dict[str, Any]) -> None:
    """5. Добавляем название"""
    if not title:
        raise RuntimeError("Шаг 5: пустое название")
    et = d(className="android.widget.EditText")
    if not et.exists(timeout=3):
        if d(text="Добавьте название").exists(timeout=1):
            d(text="Добавьте название").click()
            time.sleep(0.3)
        et = d(className="android.widget.EditText")
    if not et.exists(timeout=2):
        raise RuntimeError("Шаг 5: поле названия не найдено")
    et.click()
    time.sleep(0.25)
    try:
        et.set_text(title)
    except Exception:
        d.send_keys(title)
    time.sleep(0.3)
    _hide_keyboard(d)
    human_pause(android_cfg, scale=0.4)


def _on_upload_form(d) -> bool:
    return bool(
        d(text="Загрузить").exists(timeout=0.4)
        or d(text="Upload").exists(timeout=0.3)
        or d(text="Сохранить черновик").exists(timeout=0.3)
    )


def _option_rows(d) -> list[tuple[int, int, int, int]]:
    """Полной ширины кликабельные ряды опций (текст в a11y часто пустой)."""
    rows: list[tuple[int, int, int, int]] = []
    for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
        a = node.attrib
        if a.get("clickable") != "true":
            continue
        desc = (a.get("content-desc") or "").strip()
        text = (a.get("text") or "").strip()
        if desc in ("Развернуть", "Свернуть", "Expand", "Show less", "Show more"):
            continue
        if text in ("Развернуть", "Свернуть"):
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        # После скролла первый ряд опций может начинаться ~160–250.
        if y1 < 160 or y2 > 1380:
            continue
        if (x2 - x1) < 500 or not (55 <= (y2 - y1) <= 170):
            continue
        # Не путать с полем названия / чипами хэштегов.
        if y1 < 430 and (y2 - y1) > 120:
            continue
        rows.append((y1, y2, x1, x2))
    rows.sort()
    # unique by y
    out: list[tuple[int, int, int, int]] = []
    seen: set[int] = set()
    for r in rows:
        key = r[0] // 12
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _tap_row(d, row: tuple[int, int, int, int]) -> None:
    y1, y2, x1, x2 = row
    # правый край ряда (шеврон) — надёжнее центра
    d.click(int(x1 + (x2 - x1) * 0.88), (y1 + y2) // 2)
    time.sleep(0.9)


def _reveal_options(d) -> None:
    """Прокрутить форму, чтобы ряды опций были на экране."""
    w, h = d.window_size()
    for _ in range(3):
        if d(description="Свернуть").exists(timeout=0.3) or d(description="Развернуть").exists(
            timeout=0.3
        ):
            return
        if len(_option_rows(d)) >= 3:
            return
        d.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.38), 0.28)
        time.sleep(0.45)


def step6_expand(d, android_cfg: dict[str, Any]) -> None:
    """6. Жмём «Развернуть»"""
    _reveal_options(d)
    if d(description="Свернуть").exists(timeout=0.5) or d(text="Свернуть").exists(timeout=0.3):
        return
    if _click_label(d, "Развернуть", "Expand", "Show more", timeout=2):
        human_pause(android_cfg, scale=0.45)
        return
    # иногда desc на ViewGroup
    if d(description="Развернуть").exists(timeout=0.8):
        d(description="Развернуть").click()
        human_pause(android_cfg, scale=0.45)


def step7_set_description(d, description: str, android_cfg: dict[str, Any]) -> None:
    """7. «Добавить описание» → ввод → назад на форму"""
    if not description:
        raise RuntimeError("Шаг 7: пустое описание")
    _reveal_options(d)
    opened = _click_label(
        d, "Добавить описание", "Добавьте описание", "Add description", timeout=1.2
    )
    if not opened:
        # Порядок после Развернуть: 0 доступ, 1 аудитория, 2 описание, 3 место…
        rows = _option_rows(d)
        if len(rows) < 3:
            raise RuntimeError("Шаг 7: ряды опций не найдены (нужно «Развернуть»)")
        _tap_row(d, rows[2])
    human_pause(android_cfg, scale=0.4)
    if not d(className="android.widget.EditText").exists(timeout=3):
        raise RuntimeError("Шаг 7: экран описания не открылся")
    _type_into_edit(d, description)
    if d(text="Готово").exists(timeout=1):
        d(text="Готово").click()
    elif d(text="Done").exists(timeout=0.5):
        d(text="Done").click()
    else:
        d.press("back")
    time.sleep(0.7)
    if not _on_upload_form(d):
        d.press("back")
        time.sleep(0.5)


def _location_already_ok(d) -> bool:
    """На форме уже стоит гео (не кафе/еда)."""
    bad = ("food", "restaurant", "cafe", "vegetarian", "бар", "кафе", "ресторан")
    good = ("phuket", "пхукет", "thalang", "amphoe")
    for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
        a = node.attrib
        blob = f"{a.get('text') or ''} {a.get('content-desc') or ''}".strip().lower()
        if not blob:
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        _x1, y1, _x2, y2 = parsed
        # ряд местоположения обычно ниже названия/аудитории
        if y1 < 520 or y2 > 1200:
            continue
        if any(b in blob for b in bad):
            return False
        if any(g in blob for g in good) and ("thailand" in blob or "thalang" in blob or "phuket" in blob or "пхукет" in blob):
            return True
    return False


def step8_set_location(
    d, android_cfg: dict[str, Any], cfg: dict[str, Any]
) -> str:
    """8. «Местоположение» → поиск → гео-результат (не бизнес)."""
    aliases = list(
        cfg.get("location_aliases")
        or ["Phuket, Thailand", "Phuket", "Amphoe Thalang", "Пхукет"]
    )
    location = (cfg.get("location") or aliases[0] or "Phuket").strip()
    queries = [q for q in ([location] + aliases) if q]
    uniq_queries: list[str] = []
    for q in queries:
        if q not in uniq_queries:
            uniq_queries.append(q)
    queries = uniq_queries

    _reveal_options(d)
    if _location_already_ok(d):
        return "already_set"

    opened = _click_label(
        d, "Местоположение", "Location", "Добавить местоположение", timeout=1.2
    )
    if not opened:
        rows = _option_rows(d)
        if len(rows) < 4:
            raise RuntimeError("Шаг 8: ряд «Местоположение» не найден")
        # Доступ / Аудитория / Описание / Местоположение — индекс зависит от expand.
        # Ищем ряд с pin-like y ниже описания: обычно 3-й или 4-й.
        idx = 3 if len(rows) >= 4 else len(rows) - 1
        _tap_row(d, rows[idx])
    human_pause(android_cfg, scale=0.45)

    et = d(className="android.widget.EditText")
    if not et.exists(timeout=3):
        raise RuntimeError("Шаг 8: поиск места не открылся")

    bad_tokens = (
        "food",
        "restaurant",
        "cafe",
        "vegetarian",
        "бар",
        "кафе",
        "ресторан",
        "shop",
        "store",
        "hotel",
        "resort",
    )
    good_tokens = ("phuket", "пхукет", "thalang", "thailand", "changwat")

    picked_query = ""
    for query in queries:
        et.click()
        time.sleep(0.2)
        try:
            et.set_text(query)
        except Exception:
            d.send_keys(query)
        _hide_keyboard(d)
        time.sleep(1.0)

        best: tuple[int, int, int, int, int] | None = None  # score, y1,y2,x1,x2
        for _ in range(6):
            for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
                a = node.attrib
                if a.get("clickable") != "true":
                    continue
                if "EditText" in (a.get("class") or ""):
                    continue
                parsed = _parse_bounds(a.get("bounds", ""))
                if not parsed:
                    continue
                x1, y1, x2, y2 = parsed
                if y1 < 280 or y2 > 1300:
                    continue
                if (x2 - x1) < 500 or (y2 - y1) < 70:
                    continue
                blob = f"{a.get('text') or ''} {a.get('content-desc') or ''}".strip().lower()
                if any(b in blob for b in bad_tokens):
                    continue
                score = 0
                if any(g in blob for g in good_tokens):
                    score += 5
                if "phuket" in blob or "пхукет" in blob:
                    score += 3
                if not blob:
                    score += 1  # без текста — запасной первый ряд
                if best is None or score > best[0] or (score == best[0] and y1 < best[1]):
                    best = (score, y1, y2, x1, x2)
            if best and best[0] >= 5:
                break
            time.sleep(0.35)

        if best and best[0] >= 1:
            _tap_row(d, (best[1], best[2], best[3], best[4]))
            picked_query = query
            break

    if not picked_query:
        d.press("back")
        raise RuntimeError(f"Шаг 8: нет гео-результатов для {queries!r}")
    human_pause(android_cfg, scale=0.55)
    if not _on_upload_form(d):
        d.press("back")
        time.sleep(0.5)
    return picked_query


def step9_set_audience(d, android_cfg: dict[str, Any]) -> None:
    """9. «Укажите аудиторию» → «Видео не для детей»"""
    _reveal_options(d)
    opened = _click_label(
        d, "Укажите аудиторию", "Аудитория", "Audience", timeout=1.2
    )
    if not opened:
        rows = _option_rows(d)
        if len(rows) < 2:
            raise RuntimeError("Шаг 9: ряд «Укажите аудиторию» не найден")
        _tap_row(d, rows[1])
    human_pause(android_cfg, scale=0.5)

    # Текст радиокнопок часто не в a11y — клик по 2-му ряду экрана аудитории
    selected = _click_label(
        d,
        "Видео не для детей",
        "Это видео не для детей",
        "No, it's not made for kids",
        "Not made for kids",
        timeout=1.5,
    )
    if not selected:
        radio_rows: list[tuple[int, int, int, int]] = []
        for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
            a = node.attrib
            if a.get("clickable") != "true":
                continue
            if (a.get("content-desc") or "") in ("Подробнее", "Назад", "Learn more"):
                continue
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            if y1 < 400 or y2 > 1200:
                continue
            if (x2 - x1) < 500 or not (80 <= (y2 - y1) <= 180):
                continue
            radio_rows.append((y1, y2, x1, x2))
        radio_rows.sort()
        # unique
        uniq: list[tuple[int, int, int, int]] = []
        seen: set[int] = set()
        for r in radio_rows:
            k = r[0] // 10
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        if len(uniq) < 2:
            raise RuntimeError("Шаг 9: «Видео не для детей» не найдено")
        # 0 = для детей, 1 = не для детей
        _tap_row(d, uniq[1])
    human_pause(android_cfg, scale=0.45)
    if not _on_upload_form(d):
        d.press("back")
        time.sleep(0.5)


def step10_upload(
    d, *, confirm_post: bool, android_cfg: dict[str, Any]
) -> ChannelResult:
    """10. Жмём «Загрузить»"""
    if not (
        d(text="Загрузить").exists(timeout=3)
        or d(resourceIdMatches=r".*:id/upload_bottom_button").exists(timeout=1)
        or d(text="Upload").exists(timeout=0.5)
    ):
        return ChannelResult(
            channel="youtube_shorts",
            ok=False,
            reason="upload_button_not_found",
        )

    xml = d.dump_hierarchy()
    if "слишком длинн" in xml.lower() or "too long" in xml.lower():
        return ChannelResult(
            channel="youtube_shorts",
            ok=False,
            reason="title_too_long",
        )

    if not confirm_post:
        return ChannelResult(
            channel="youtube_shorts",
            ok=True,
            skipped=True,
            reason="stopped_before_publish",
            note="Готово к загрузке — «Загрузить» не нажата (--live)",
        )

    btn = d(resourceIdMatches=r".*:id/upload_bottom_button")
    if btn.exists(timeout=1):
        if btn.info.get("enabled") is False:
            return ChannelResult(
                channel="youtube_shorts",
                ok=False,
                reason="upload_button_disabled",
            )
        btn.click()
    elif d(text="Загрузить").exists(timeout=1):
        d(text="Загрузить").click()
    else:
        d(text="Upload").click()
    human_pause(android_cfg, scale=1.2)

    for _ in range(15):
        xml = d.dump_hierarchy()
        if "Загрузить" not in xml and "Upload" not in xml:
            return ChannelResult(
                channel="youtube_shorts",
                ok=True,
                note="Загрузка Shorts запущена",
            )
        time.sleep(0.7)
    return ChannelResult(
        channel="youtube_shorts",
        ok=True,
        note="Нажато «Загрузить»",
    )


class YoutubeShortsChannel:
    name = "youtube_shorts"

    def publish(
        self,
        job: PublishJob,
        *,
        dry_run: bool,
        android_cfg: dict[str, Any],
        publisher_cfg: dict[str, Any],
        confirm_post: bool = False,
    ) -> ChannelResult:
        if not job.video_url and not job.local_video and not job.device_video:
            return ChannelResult(
                channel=self.name, ok=False, skipped=True, reason="no video"
            )

        cfg = _yt_cfg(publisher_cfg, android_cfg)
        package = _package(android_cfg)
        album = cfg.get("album") or ALBUM_DEFAULT
        max_title = int(cfg.get("max_title_chars") or TITLE_MAX)
        max_desc = int(cfg.get("max_description_chars") or DESC_MAX)
        title, description = _build_youtube_copy(
            job, max_title=max_title, max_desc=max_desc
        )
        if not title or not description:
            return ChannelResult(
                channel=self.name,
                ok=False,
                skipped=True,
                reason="missing title or description",
                note="Нужны название и «Описание соц.сети»",
            )

        note = (
            f"YouTube Shorts: title={title!r}; desc_len={len(description)}; "
            f"album={album}; location={cfg.get('location') or 'Phuket'}"
        )

        if dry_run:
            return ChannelResult(
                channel=self.name, ok=True, skipped=True, reason="dry-run", note=note
            )
        if not (android_cfg.get("ui_automation") or {}).get("enabled"):
            return ChannelResult(
                channel=self.name,
                ok=True,
                skipped=True,
                reason="ui_automation_disabled",
                note=note,
            )

        try:
            print("YT: connect + open", flush=True)
            d = connect_device(android_cfg)
            ensure_unlocked(d)
            d.screen_on()
            _open_youtube(d, package, android_cfg)
            print("YT: 1 plus/gallery", flush=True)
            step1_press_plus(d, android_cfg)
            print("YT: 2 select video", flush=True)
            step2_select_video(d, android_cfg, album=album, object_id=job.object_id)
            print("YT: 3-4 gotovo/dalee", flush=True)
            step3_press_gotovo(d, android_cfg)
            step4_press_dalee(d, android_cfg)
            print("YT: 5 title", flush=True)
            step5_set_title(d, title, android_cfg)
            step6_expand(d, android_cfg)
            # Только: название → описание → не для детей → локация → загрузить.
            print("YT: 7 description", flush=True)
            step7_set_description(d, description, android_cfg)
            print("YT: 9 audience not-for-kids", flush=True)
            step9_set_audience(d, android_cfg)
            print("YT: 8 location", flush=True)
            loc = step8_set_location(d, android_cfg, cfg)
            note = f"{note}; location_set={loc}"
            print(f"YT: 10 upload live={confirm_post} loc={loc}", flush=True)
            result = step10_upload(
                d, confirm_post=confirm_post, android_cfg=android_cfg
            )
            result = attach_post_url(
                d,
                result,
                self.name,
                android_cfg,
                confirm_post=confirm_post,
            )
            result.note = f"{(result.note or '')}; {note}".strip("; ")
            return result
        except Exception as e:
            return ChannelResult(
                channel=self.name,
                ok=False,
                reason=str(e),
                note=note,
            )
