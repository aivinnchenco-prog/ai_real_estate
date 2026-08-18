from __future__ import annotations

import re
import time
import urllib.request
from typing import Any
from xml.etree import ElementTree as ET

from ..android.ui import connect_device, dismiss_permissions, human_pause
from ..android.vision_fallback import get_vision_fallback
from ..models import PublishJob
from ..state import load_state, mark_fb_group_published
from . import fb_marketplace as mp
from ._fb_publish import uploads_still_running
from .base import (
    ChannelResult,
    carousel_bounds_pick_order,
    count_selected_gallery_photos,
    device_media_album,
    gallery_selection_state,
    require_designed_carousel,
)
from ._post_url import attach_post_url

PKG_DEFAULT = "com.facebook.katana"
ALBUM_DEFAULT = "publisher_social"
_DISCUSSION_PUBLISH_LABELS = ("Опубликовать", "Отправить", "Post", "Publish", "Send", "Надіслати")


def _package(android_cfg: dict[str, Any]) -> str:
    return android_cfg.get("packages", {}).get("facebook", PKG_DEFAULT)


def _grp_cfg(publisher_cfg: dict[str, Any], android_cfg: dict[str, Any]) -> dict[str, Any]:
    base = dict(publisher_cfg.get("fb_groups") or {})
    base.update(android_cfg.get("fb_groups") or {})
    return base


def _post_publish_pause(cfg: dict[str, Any]) -> None:
    """Пауза после «Опубликовать», чтобы FB успел загрузить медиа."""
    sec = float(cfg.get("post_publish_pause_seconds") or 0)
    if sec > 0:
        time.sleep(sec)


def _parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _extract_group_id(url: str) -> str | None:
    m = re.search(r"facebook\.com/groups/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"fb://group/?\?id=(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"fb://group/(\d+)", url)
    if m:
        return m.group(1)
    return None


def resolve_group_id(url: str) -> str:
    """Numeric group id, или canonical https URL для vanity slug (/groups/name/)."""
    direct = _extract_group_id(url)
    if direct:
        return direct
    if "share/g/" in url or "share/g%" in url:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PublisherSocial/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            final = resp.geturl()
        gid = _extract_group_id(final)
        if gid:
            return gid
    normalized = url.strip().rstrip("/") + "/"
    m = re.search(r"facebook\.com/groups/([^/?#]+)/", normalized)
    if m and not m.group(1).isdigit():
        if normalized.startswith("http"):
            return normalized
        return f"https://www.facebook.com/groups/{m.group(1)}/"
    raise RuntimeError(f"Не удалось извлечь ID группы из URL: {url}")


_JOIN_LABELS = (
    "Вступить в группу",
    "Join group",
    "Join",
    "Приєднатися до групи",
    "Приєднатися",
)
_JOIN_PENDING_MARKERS = (
    "Запрос отправлен",
    "Request sent",
    "На рассмотрении",
    "Ожидает одобрения",
    "Pending",
)
_JOIN_SUBMIT_LABELS = (
    "Отправить",
    "Подать запрос",
    "Подать заявку",
    "Send",
    "Submit",
    "Send request",
    "Надіслати",
    "Подати запит",
    "Відправити",
    "Готово",
    "Done",
)


def _submit_join_dialog(d, android_cfg: dict[str, Any]) -> None:
    for _ in range(8):
        xml = d.dump_hierarchy()
        if 'checked="false"' in xml:
            for node in ("checkbox", "CheckBox"):
                if d(classNameContains=node, checked=False).exists(timeout=0.2):
                    d(classNameContains=node, checked=False).click()
                    human_pause(android_cfg, scale=0.5)
        for hint in ("правил", "rules", "погодж", "accept", "згоден"):
            if d(textContains=hint).exists(timeout=0.2):
                d(textContains=hint).click()
                human_pause(android_cfg, scale=0.4)
        for label in _JOIN_SUBMIT_LABELS:
            if d(text=label).exists(timeout=0.35):
                d(text=label).click()
                human_pause(android_cfg, scale=1.0)
                return
            if d(textContains=label).exists(timeout=0.25):
                d(textContains=label).click()
                human_pause(android_cfg, scale=1.0)
                return
        human_pause(android_cfg, scale=0.6)


def _try_join_group(d, android_cfg: dict[str, Any], group_id: str) -> bool:
    """Вступить в группу на телефоне. False = заявка отправлена, но ещё не одобрена."""
    xml = d.dump_hierarchy()
    if any(marker in xml for marker in _JOIN_PENDING_MARKERS):
        return False

    clicked = False
    for label in _JOIN_LABELS:
        if d(text=label).exists(timeout=0.35):
            d(text=label).click()
            clicked = True
            break
        if d(textContains=label).exists(timeout=0.25):
            d(textContains=label).click()
            clicked = True
            break
        if d(descriptionContains=label).exists(timeout=0.25):
            d(descriptionContains=label).click()
            clicked = True
            break
    if not clicked:
        return False

    human_pause(android_cfg, scale=1.5)
    _submit_join_dialog(d, android_cfg)

    for _ in range(30):
        xml = d.dump_hierarchy()
        if any(marker in xml for marker in _JOIN_PENDING_MARKERS):
            return False
        if (
            "В группе" in xml
            or "Напишите что-нибудь" in xml
            or "Write something" in xml
            or "Что вы продаете?" in xml
        ):
            return True
        time.sleep(0.5)
    return False


def _open_group(d, package: str, group_id: str, android_cfg: dict[str, Any]) -> None:
    from ..android.ui import ensure_unlocked

    d.press("home")
    time.sleep(0.35)
    d.app_stop(package)
    time.sleep(0.45)
    d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    human_pause(android_cfg, scale=1.0)
    ensure_unlocked(d)
    if group_id.startswith("http"):
        open_uri = group_id
    else:
        open_uri = f"fb://group/{group_id}"
    d.shell(f'am start -a android.intent.action.VIEW -d "{open_uri}"')
    human_pause(android_cfg, scale=1.3)
    ensure_unlocked(d)
    dismiss_permissions(d)
    # ждём композер / property UI; если не в группе — вступаем автоматически
    for _ in range(24):
        xml = d.dump_hierarchy()
        if "Вступить в группу" in xml and "В группе" not in xml:
            _try_join_group(d, android_cfg, group_id)
        if (
            "Напишите что-нибудь" in xml
            or "Что вы продаете?" in xml
            or "Продажа и аренда недвижимости" in xml
        ):
            return
        # шапка уже есть — чуть вниз к композеру
        if "В группе" in xml or "Общедоступная группа" in xml:
            w, h = d.window_size()
            d.swipe(w // 2, int(h * 0.55), w // 2, int(h * 0.35), 0.2)
        time.sleep(0.45)
    # не падаем здесь — _open_discussion_compose проверит поле


def _composer_visible(d) -> bool:
    return bool(
        d(textContains="Напишите что-нибудь").exists(timeout=0.35)
        or d(descriptionContains="Напишите что-нибудь").exists(timeout=0.25)
        or d(text="Что вы продаете?").exists(timeout=0.25)
        or d(textContains="Write something").exists(timeout=0.2)
    )


def _ensure_group_compose(d, android_cfg: dict[str, Any]) -> None:
    for _ in range(6):
        if _composer_visible(d):
            break
        w, h = d.window_size()
        d.swipe(w // 2, int(h * 0.6), w // 2, int(h * 0.35), 0.22)
        time.sleep(0.4)
    for label in (
        "Напишите что-нибудь...",
        "Напишите что-нибудь…",
        "Напишите что-нибудь",
        "Write something",
        "Создать публикацию",
        "Что вы продаете?",
    ):
        if d(text=label).exists(timeout=0.7):
            d(text=label).click()
            human_pause(android_cfg, scale=0.8)
            return
        if d(textContains=label.rstrip(".…?")).exists(timeout=0.4):
            d(textContains=label.rstrip(".…?")).click()
            human_pause(android_cfg, scale=0.8)
            return
        if d(descriptionContains=label.rstrip(".…?")).exists(timeout=0.4):
            d(descriptionContains=label.rstrip(".…?")).click()
            human_pause(android_cfg, scale=0.8)
            return
    if d(text="Продать").exists(timeout=0.8):
        d(text="Продать").click()
        human_pause(android_cfg, scale=0.8)


def _detect_mode(d) -> str:
    xml = d.dump_hierarchy()
    if any(
        x in xml
        for x in (
            "Что вы продаете?",
            "Продажа/аренда недвижимости",
            "Продажа и аренда недвижимости",
            "Добавить фото",
            "Количество спален",
        )
    ):
        return "property"
    if (
        "Напишите что-нибудь" in xml
        or d(textContains="Напишите что-нибудь").exists(timeout=0.5)
        or d(descriptionContains="Напишите что-нибудь").exists(timeout=0.4)
        or d(text="Галерея").exists(timeout=0.4)
        or d(description="Галерея").exists(timeout=0.3)
    ):
        return "discussion"
    return "unknown"


def _open_property_form(d, android_cfg: dict[str, Any]) -> None:
    if d(text="Что вы продаете?").exists(timeout=3):
        d(text="Что вы продаете?").click()
    else:
        d(descriptionContains="Что вы продаете").click()
    human_pause(android_cfg)
    label = "Продажа и аренда недвижимости"
    if d(text=label).exists(timeout=4):
        d(text=label).click()
    elif d(descriptionContains=label).exists(timeout=1):
        d(descriptionContains=label).click()
    else:
        raise RuntimeError(f"Пункт «{label}» не найден в группе")
    human_pause(android_cfg, scale=1.2)
    for _ in range(20):
        if (
            d(text="Добавить фото").exists(timeout=0.4)
            or d(text="Продажа/аренда недвижимости").exists(timeout=0.3)
        ):
            return
        time.sleep(0.4)
    raise RuntimeError("Форма объявления в группе не открылась")


def _publish_label_matches(label: str) -> bool:
    clean = label.strip()
    if not clean:
        return False
    low = clean.lower()
    for item in _DISCUSSION_PUBLISH_LABELS:
        if clean == item or item.lower() in low:
            return True
    return False


def _discussion_bottom_publish_targets(
    xml: str,
    *,
    screen_h: int,
    screen_w: int,
) -> list[tuple[int, int, int, int]]:
    """
    «Опубликовать» в нижней панели композера (правый нижний угол).

    Игнорируем одноимённую кнопку в верхнем тулбаре — она не публикует пост.
    """
    min_y = int(screen_h * 0.72)
    min_x = int(screen_w * 0.50)
    out: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()

    def _add(parsed: tuple[int, int, int, int]) -> None:
        x1, y1, x2, y2 = parsed
        if y1 < min_y or ((x1 + x2) // 2) < min_x:
            return
        key = (x1 // 40, y1 // 40)
        if key in seen:
            return
        seen.add(key)
        out.append(parsed)

    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        label = (a.get("text") or a.get("content-desc") or "").strip()
        blob = label.lower()
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        if _publish_label_matches(label):
            _add(parsed)
            continue
        if "button" not in (a.get("class") or "").lower():
            continue
        if a.get("clickable") != "true":
            continue
        if any(token in blob for token in ("опублик", "post", "publish")):
            _add(parsed)

    out.sort(key=lambda b: (b[3], b[2]), reverse=True)
    return out


def _discussion_bottom_publish_visible(d) -> bool:
    w, h = d.window_size()
    xml = d.dump_hierarchy() or ""
    return bool(_discussion_bottom_publish_targets(xml, screen_h=h, screen_w=w))


def _discussion_bottom_publish_point(d) -> tuple[int, int] | None:
    w, h = d.window_size()
    xml = d.dump_hierarchy() or ""
    targets = _discussion_bottom_publish_targets(xml, screen_h=h, screen_w=w)
    if not targets:
        return None
    x1, y1, x2, y2 = targets[0]
    return (x1 + x2) // 2, (y1 + y2) // 2


def _discussion_coordinate_publish_points(
    w: int,
    h: int,
) -> list[tuple[int, int]]:
    """
  Fallback taps when a11y misses the sticky footer button.

  На Samsung/Facebook кнопка «Опубликовать» ~2 см выше физического низа экрана
  (не в углу и не в зоне жестов) — не тапаем ниже ~0.92h.
  """
    return [
        (int(w * 0.90), int(h * 0.86)),
        (int(w * 0.86), int(h * 0.88)),
        (int(w * 0.92), int(h * 0.84)),
        (int(w * 0.84), int(h * 0.87)),
    ]


def _discussion_hide_keyboard(d, android_cfg: dict[str, Any]) -> None:
    try:
        d.hide_keyboard()
    except Exception:
        pass
    human_pause(android_cfg, scale=0.35)


def _discussion_compose_open(d) -> bool:
    """Уже на экране создания поста в группе."""
    if _discussion_bottom_publish_visible(d):
        return True
    return bool(
        d(text="Галерея").exists(timeout=0.35)
        or d(description="Галерея").exists(timeout=0.25)
        or d(textContains="общедоступную публикацию").exists(timeout=0.25)
        or d(descriptionContains="Название публикации").exists(timeout=0.25)
        or d(descriptionContains="Местоположение").exists(timeout=0.2)
    )


def _discussion_ensure_compose_ready(d, android_cfg: dict[str, Any]) -> None:
    _discussion_hide_keyboard(d, android_cfg)
    if _discussion_compose_open(d):
        return
    for _ in range(4):
        d.press("back")
        human_pause(android_cfg, scale=0.45)
        _discussion_hide_keyboard(d, android_cfg)
        if _discussion_compose_open(d):
            return
    raise RuntimeError("FB Groups: не удалось вернуться к экрану публикации")


def _discussion_wait_for_bottom_publish(
    d,
    android_cfg: dict[str, Any],
    *,
    timeout_sec: float = 120,
) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        _discussion_hide_keyboard(d, android_cfg)
        if _discussion_bottom_publish_visible(d):
            return True
        xml = d.dump_hierarchy() or ""
        if uploads_still_running(xml):
            time.sleep(2.0)
            continue
        time.sleep(1.5)
    return _discussion_bottom_publish_visible(d)


def _discussion_tap_bottom_publish(d, android_cfg: dict[str, Any]) -> bool:
    """
    Нажать «Опубликовать» в правом нижнем углу.

    Сначала координаты (sticky footer), затем a11y-цель в нижней полосе экрана.
    """
    _discussion_hide_keyboard(d, android_cfg)
    w, h = d.window_size()
    human_pause(android_cfg, scale=0.5)

    for attempt in range(10):
        point = _discussion_bottom_publish_point(d)
        if point is not None:
            d.click(*point)
            time.sleep(1.0)
            if not _discussion_bottom_publish_visible(d):
                return True

        for cx, cy in _discussion_coordinate_publish_points(w, h):
            d.click(cx, cy)
            time.sleep(0.9)
            if not _discussion_bottom_publish_visible(d):
                return True

        if attempt in (4, 7):
            d.swipe(w // 2, int(h * 0.68), w // 2, int(h * 0.38), 0.18)
            time.sleep(0.45)
        time.sleep(0.25)
    vf = get_vision_fallback(android_cfg)
    if vf and vf.tap_publish(d):
        time.sleep(1.0)
        if not _discussion_bottom_publish_visible(d):
            return True
    return False


def _open_discussion_compose(d, android_cfg: dict[str, Any]) -> None:
    # _ensure_group_compose мог уже открыть композер
    if _discussion_compose_open(d):
        return

    for _ in range(6):
        if (
            d(textContains="Напишите что-нибудь").exists(timeout=0.5)
            or d(descriptionContains="Напишите что-нибудь").exists(timeout=0.35)
            or d(textContains="Write something").exists(timeout=0.3)
        ):
            break
        w, h = d.window_size()
        d.swipe(w // 2, int(h * 0.62), w // 2, int(h * 0.38), 0.22)
        time.sleep(0.4)

    if d(textContains="Напишите что-нибудь").exists(timeout=1.2):
        d(textContains="Напишите что-нибудь").click()
        human_pause(android_cfg)
    elif d(descriptionContains="Напишите что-нибудь").exists(timeout=0.6):
        d(descriptionContains="Напишите что-нибудь").click()
        human_pause(android_cfg)
    elif d(textContains="Write something").exists(timeout=0.5):
        d(textContains="Write something").click()
        human_pause(android_cfg)
    elif d(description="Фото/видео").exists(timeout=0.6):
        d(description="Фото/видео").click()
        human_pause(android_cfg)
    else:
        raise RuntimeError("Не найдено поле «Напишите что-нибудь...» в группе")

    if not _discussion_compose_open(d):
        # дать UI дорисовать
        time.sleep(0.8)
    if not _discussion_compose_open(d):
        raise RuntimeError("Композер группы не открылся после клика")


def _fb_photo_checkbox_point(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    """FB multi-select badge sits on the top-right of each thumbnail."""
    x1, y1, x2, y2 = bounds
    return int(x1 + (x2 - x1) * 0.88), int(y1 + (y2 - y1) * 0.12)


def _fb_photo_center_point(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bounds
    return (x1 + x2) // 2, (y1 + y2) // 2


def _looks_like_gallery_thumbnail(desc: str) -> bool:
    low = (desc or "").lower()
    if not low:
        return False
    if any(
        token in low
        for token in (
            "фото",
            "photo",
            "image",
            "изображен",
            "дата и время",
            "photo taken on",
            "slide_",
            ".jpg",
            ".jpeg",
            ".webp",
            ".png",
        )
    ):
        return True
    return bool(re.search(r"slide_\d+", low))


def _grid_min_y() -> int:
    return 260


def _tap_discussion_thumbnail(
    d,
    bounds: tuple[int, int, int, int],
    *,
    mode: str,
) -> None:
    """Tap checkbox badge; fallback to center if FB ignores corner taps."""
    cb_x, cb_y = _fb_photo_checkbox_point(bounds)
    cx, cy = _fb_photo_center_point(bounds)
    if mode == "long_press":
        d.long_click(cx, cy)
        time.sleep(0.55)
        return
    d.click(cb_x, cb_y)
    time.sleep(0.35)
    state = _selection_state_for_bounds(d.dump_hierarchy() or "", bounds)
    if state != "selected":
        d.click(cx, cy)
        time.sleep(0.35)
        state = _selection_state_for_bounds(d.dump_hierarchy() or "", bounds)
    if state != "selected":
        d.click(cb_x, cb_y)
        time.sleep(0.3)


def _bounds_match(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
    *,
    tolerance: int = 10,
) -> bool:
    return all(abs(left[i] - right[i]) <= tolerance for i in range(4))


def _selection_state_for_bounds(
    xml: str,
    bounds: tuple[int, int, int, int],
) -> str | None:
    for node in ET.fromstring(xml).iter("node"):
        parsed = _parse_bounds(node.attrib.get("bounds", ""))
        if not parsed or not _bounds_match(parsed, bounds):
            continue
        desc = node.attrib.get("content-desc") or node.attrib.get("text") or ""
        state = gallery_selection_state(desc)
        if state:
            return state
    return None


def _discussion_selected_count(xml: str) -> int:
    ui_count = _read_discussion_selected_count_from_xml(xml)
    if ui_count is not None:
        return ui_count
    return count_selected_gallery_photos(xml)


def _read_discussion_selected_count_from_xml(xml: str) -> int | None:
    for node in ET.fromstring(xml).iter("node"):
        for attr in ("text", "content-desc"):
            raw = node.attrib.get(attr) or ""
            low = raw.lower()
            match = re.search(r"(?:выбрано|selected)\s*(\d+)", low)
            if match:
                return int(match.group(1))
    return None


def _collect_discussion_photo_bounds(
    xml: str,
    *,
    object_id: str = "",
) -> list[tuple[int, int, int, int]]:
    """Gallery thumbnails as (x1,y1,x2,y2), slide_01 (price hook) first."""
    bounds_list: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()
    oid = object_id.lower() if object_id else ""

    def _blob_matches_object(blob: str) -> bool:
        if not oid:
            return True
        low = blob.lower()
        return oid in low or f"{oid}_slide" in low

    def _add(parsed: tuple[int, int, int, int]) -> None:
        x1, y1, x2, y2 = parsed
        if y1 < _grid_min_y() or (x2 - x1) < 60 or (y2 - y1) < 60:
            return
        key = (x1 // 80, y1 // 80)
        if key in seen:
            return
        seen.add(key)
        bounds_list.append(parsed)

    def _node_blob(a: dict[str, str]) -> str:
        return (a.get("content-desc") or "") + " " + (a.get("text") or "")

    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        desc = _node_blob(a)
        if not _blob_matches_object(desc):
            continue
        low = desc.lower()
        if any(x in low for x in ("сделать", "camera", "назад", "back", "готово", "done")):
            continue
        if not _looks_like_gallery_thumbnail(desc):
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if parsed:
            _add(parsed)

    if len(bounds_list) < 2:
        bounds_list.clear()
        seen.clear()
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            desc = _node_blob(a)
            if oid and not _blob_matches_object(desc):
                continue
            low = desc.lower()
            if any(
                x in low
                for x in (
                    "сделать",
                    "camera",
                    "назад",
                    "back",
                    "готово",
                    "done",
                    "подборк",
                    "альбом",
                    "album",
                )
            ):
                continue
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            w_cell, h_cell = x2 - x1, y2 - y1
            if y1 < _grid_min_y() or w_cell < 80 or h_cell < 80:
                continue
            if abs(w_cell - h_cell) > 120:
                continue
            if a.get("clickable") == "true" or _looks_like_gallery_thumbnail(desc):
                _add(parsed)

    if len(bounds_list) < 1:
        bounds_list.clear()
        seen.clear()
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            desc = _node_blob(a)
            if oid and not _blob_matches_object(desc):
                continue
            if not _looks_like_gallery_thumbnail(desc):
                continue
            parsed = _parse_bounds(a.get("bounds", ""))
            if parsed:
                _add(parsed)

    return carousel_bounds_pick_order(bounds_list)


def _arm_discussion_multi_select(
    d,
    android_cfg: dict[str, Any],
    first_bounds: tuple[int, int, int, int] | None,
) -> str:
    """Return 'button' or 'long_press' when multi-select is armed."""
    labels = (
        "Выбрать несколько",
        "Выбрать несколько фото",
        "Select multiple",
        "Select Multiple",
        "Select multiple photos",
        "Несколько",
    )
    for label in labels:
        if d(descriptionContains=label).exists(timeout=0.7):
            d(descriptionContains=label).click()
            human_pause(android_cfg, scale=0.4)
            return "button"
        if d(text=label).exists(timeout=0.4):
            d(text=label).click()
            human_pause(android_cfg, scale=0.4)
            return "button"
    xml = d.dump_hierarchy() or ""
    for node in ET.fromstring(xml).iter("node"):
        if node.attrib.get("clickable") != "true":
            continue
        blob = (
            (node.attrib.get("text") or "")
            + " "
            + (node.attrib.get("content-desc") or "")
        ).lower()
        if "несколько" not in blob and "multiple" not in blob:
            continue
        parsed = _parse_bounds(node.attrib.get("bounds", ""))
        if not parsed:
            continue
        cx = (parsed[0] + parsed[2]) // 2
        cy = (parsed[1] + parsed[3]) // 2
        d.click(cx, cy)
        human_pause(android_cfg, scale=0.4)
        return "button"
    if first_bounds is not None:
        cx, cy = _fb_photo_checkbox_point(first_bounds)
        d.long_click(cx, cy)
        human_pause(android_cfg, scale=0.6)
        return "long_press"
    vf = get_vision_fallback(android_cfg)
    if vf and vf.enable_multi_select(d):
        return "button"
    return ""


def _read_discussion_selected_count(d) -> int | None:
    xml = d.dump_hierarchy() or ""
    counted = _discussion_selected_count(xml)
    return counted if counted > 0 else None


def _bounds_grid_key(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    return bounds[0] // 80, bounds[1] // 80


def _album_search_labels(album: str, object_id: str) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for raw in (album, object_id, ALBUM_DEFAULT):
        if raw and raw not in seen:
            labels.append(raw)
            seen.add(raw)
    if album and "carousel_" in album:
        tail = album.split("carousel_", 1)[-1]
        if tail and tail not in seen:
            labels.append(tail)
            seen.add(tail)
        parts = tail.split("_")
        if len(parts) >= 3 and parts[0] in ("A", "F") and parts[1].isdigit():
            short = "_".join(parts[:3])
            if short not in seen:
                labels.append(short)
                seen.add(short)
    return labels


_GENERAL_GALLERY_TITLES = (
    "все изображения",
    "all photos",
    "all images",
    "недавние",
    "recents",
    "recent",
)


def _discussion_on_general_gallery(xml: str) -> bool:
    """True when gallery header shows «Все изображения» / «Недавние», not object folder."""
    for node in ET.fromstring(xml).iter("node"):
        for attr in ("text", "content-desc"):
            raw = (node.attrib.get(attr) or "").strip()
            if not raw:
                continue
            low = raw.lower()
            if not any(low == m or low.startswith(m + " ") for m in _GENERAL_GALLERY_TITLES):
                continue
            parsed = _parse_bounds(node.attrib.get("bounds", ""))
            if parsed and parsed[1] < 420:
                return True
    return False


def _discussion_header_has_label(xml: str, label: str) -> bool:
    if not label:
        return False
    for node in ET.fromstring(xml).iter("node"):
        blob = (node.attrib.get("text") or "") + " " + (node.attrib.get("content-desc") or "")
        if label not in blob:
            continue
        parsed = _parse_bounds(node.attrib.get("bounds", ""))
        if parsed and parsed[1] < 420:
            return True
    return False


def _verify_object_album(d, object_id: str, album: str) -> bool:
    xml = d.dump_hierarchy() or ""
    if _discussion_on_general_gallery(xml):
        return False
    for label in _album_search_labels(album, object_id):
        if label and label != ALBUM_DEFAULT and _discussion_header_has_label(xml, label):
            return True
    if object_id and _discussion_object_photo_hits(xml, object_id) >= 2:
        return True
    return False


def _discussion_object_photo_hits(xml: str, object_id: str) -> int:
    if not object_id:
        return 0
    oid = object_id.lower()
    hits = 0
    for node in ET.fromstring(xml).iter("node"):
        blob = (
            (node.attrib.get("text") or "")
            + " "
            + (node.attrib.get("content-desc") or "")
        ).lower()
        if oid in blob or f"{oid}_slide" in blob:
            hits += 1
    return hits


def _open_discussion_album_picker(d) -> bool:
    """Open album/folder dropdown from FB gallery (Samsung: tap «Все изображения»)."""
    if d(descriptionContains="Выбор альбома").exists(timeout=1.2):
        d(descriptionContains="Выбор альбома").click()
        time.sleep(1.0)
        return True
    for title in (
        "Все изображения",
        "All photos",
        "All images",
        "Недавние",
        "Recents",
    ):
        if d(text=title).exists(timeout=0.7):
            d(text=title).click()
            time.sleep(1.0)
            return True
        if d(description=title).exists(timeout=0.5):
            d(description=title).click()
            time.sleep(1.0)
            return True
    if d(text="Галерея").exists(timeout=0.8):
        d(text="Галерея").click()
        time.sleep(1.0)
        return True
    if d(descriptionContains="Галерея").exists(timeout=0.5):
        d(descriptionContains="Галерея").click()
        time.sleep(1.0)
        return True
    return False


def _tap_discussion_album_label(d, label: str) -> bool:
    if not label or label == ALBUM_DEFAULT:
        return False
    if d(text=label).exists(timeout=1.0):
        d(text=label).click()
        return True
    if d(description=label).exists(timeout=0.6):
        d(description=label).click()
        return True
    if d(textContains=label).exists(timeout=0.8):
        d(textContains=label).click()
        return True
    if d(descriptionContains=label).exists(timeout=0.6):
        d(descriptionContains=label).click()
        return True
    return False


def _discussion_gallery_tile_count(d) -> int:
    return len(_collect_discussion_photo_bounds(d.dump_hierarchy() or ""))


def _scroll_discussion_gallery_to_top(d) -> None:
    w, h = d.window_size()
    for _ in range(4):
        d.swipe(w // 2, int(h * 0.28), w // 2, int(h * 0.72), 0.22)
        time.sleep(0.25)


def _open_discussion_gallery_album(
    d,
    android_cfg: dict[str, Any],
    *,
    album: str,
    object_id: str,
    min_tiles: int = 2,
) -> None:
    """Open object carousel folder — never stay on «Все изображения» / «Недавние»."""
    labels = _album_search_labels(album, object_id)

    if _verify_object_album(d, object_id, album):
        _scroll_discussion_gallery_to_top(d)
        return

    def _leave_album_radio_list() -> None:
        if d(text="Telegram").exists(timeout=0.3):
            d.press("back")
            time.sleep(0.8)

    def _pick_from_list() -> bool:
        for label in labels:
            if not _tap_discussion_album_label(d, label):
                continue
            time.sleep(1.0)
            _leave_album_radio_list()
            if _verify_object_album(d, object_id, album):
                _scroll_discussion_gallery_to_top(d)
                return True
        return False

    for _ in range(2):
        if _open_discussion_album_picker(d):
            if _pick_from_list():
                return
            for _ in range(6):
                w, h = d.window_size()
                d.swipe(w // 2, int(h * 0.75), w // 2, int(h * 0.35), 0.28)
                time.sleep(0.45)
                if _pick_from_list():
                    return
        elif _pick_from_list():
            return
        time.sleep(0.5)

    if _verify_object_album(d, object_id, album):
        _scroll_discussion_gallery_to_top(d)
        return
    vf = get_vision_fallback(android_cfg)
    if vf and vf.open_album(d, album):
        time.sleep(1.0)
        if _verify_object_album(d, object_id, album):
            _scroll_discussion_gallery_to_top(d)
            return
    raise RuntimeError(
        f"FB Groups: альбом объекта не найден ({album!r}, object={object_id!r}). "
        "Сначала queue --live или prepare --push-media"
    )


def _collect_discussion_photo_bounds_live(
    d,
    *,
    max_images: int,
    object_id: str = "",
) -> list[tuple[int, int, int, int]]:
    max_images = max(1, min(int(max_images), 10))
    seen: set[tuple[int, int]] = set()
    ordered: list[tuple[int, int, int, int]] = []

    def _merge(xml: str) -> None:
        nonlocal ordered
        for bounds in _collect_discussion_photo_bounds(xml, object_id=object_id):
            key = _bounds_grid_key(bounds)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(bounds)
        ordered = carousel_bounds_pick_order(ordered)

    w, h = d.window_size()
    _merge(d.dump_hierarchy() or "")

    stale = 0
    max_scrolls = max(18, max_images * 2)
    for _ in range(max_scrolls):
        if len(ordered) >= max_images:
            return ordered[:max_images]
        before = len(seen)
        d.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.32), 0.28)
        time.sleep(0.45)
        _merge(d.dump_hierarchy() or "")
        if len(ordered) >= max_images:
            return ordered[:max_images]
        if len(seen) == before:
            stale += 1
            if len(ordered) >= max_images:
                break
            if stale >= 5:
                break
        else:
            stale = 0
    return ordered[:max_images]


def _select_discussion_photos(
    d,
    android_cfg: dict[str, Any],
    *,
    max_images: int,
    object_id: str = "",
) -> int:
    """
    FB Groups discussion: enable multi-select, then tap each thumbnail badge once.

    FB often does not expose per-thumbnail selected state in a11y XML, so we also
    track which grid cells were already tapped in this session — never toggle twice.
    """
    max_images = max(1, min(int(max_images), 10))
    time.sleep(0.8)
    preview = _collect_discussion_photo_bounds(
        d.dump_hierarchy() or "", object_id=object_id
    )
    mode = _arm_discussion_multi_select(
        d, android_cfg, preview[0] if preview else None
    )
    if not mode:
        raise RuntimeError(
            "FB Groups: не удалось включить выбор нескольких фото в галерее."
        )

    bounds_list = _collect_discussion_photo_bounds_live(
        d, max_images=max_images, object_id=object_id
    )
    if not bounds_list:
        vf = get_vision_fallback(android_cfg)
        if vf:
            vf.enable_multi_select(d)
            n = vf.select_photo_thumbnails(d, max_images)
            if n >= min(2, max_images):
                return n
        raise RuntimeError("FB Groups: в альбоме карусели не найдены фото")
    targets = bounds_list[:max_images]

    tapped_keys: set[tuple[int, int]] = set()
    if mode == "long_press" and targets:
        # _arm_discussion_multi_select already long-pressed the first cell.
        tapped_keys.add(_bounds_grid_key(targets[0]))

    attempts = 0
    limit = max(max_images * 3, 12)
    while len(tapped_keys) < len(targets) and attempts < limit:
        xml = d.dump_hierarchy() or ""
        selected = _discussion_selected_count(xml)
        if selected is not None and selected >= len(targets):
            break

        progressed = False
        for bounds in targets:
            key = _bounds_grid_key(bounds)
            if key in tapped_keys:
                continue
            state = _selection_state_for_bounds(xml, bounds)
            if state == "selected":
                tapped_keys.add(key)
                progressed = True
                continue
            _tap_discussion_thumbnail(d, bounds, mode="button")
            tapped_keys.add(key)
            progressed = True
            break
        if not progressed:
            break
        attempts += 1

    final_xml = d.dump_hierarchy() or ""
    counter = _read_discussion_selected_count_from_xml(final_xml)
    if counter is not None:
        ui_count = counter
    else:
        ui_count = _discussion_selected_count(final_xml)
    if len(tapped_keys) >= len(targets):
        ui_count = max(ui_count, len(targets))
    if ui_count < len(targets):
        vf = get_vision_fallback(android_cfg)
        if vf:
            extra = vf.select_photo_thumbnails(d, len(targets) - ui_count)
            ui_count += extra
    if len(targets) >= 2 and ui_count < 2:
        raise RuntimeError(
            f"FB Groups: multi-select не сработал — выбрано {ui_count} из {len(targets)}"
        )
    if ui_count < len(targets):
        raise RuntimeError(
            f"FB Groups: выбрано {ui_count} фото, ожидалось {len(targets)}"
        )
    return ui_count


def _discussion_add_photos(
    d,
    android_cfg: dict[str, Any],
    *,
    album: str,
    object_id: str,
    max_images: int,
) -> int:
    max_images = max(1, min(int(max_images), 10))
    if d(text="Галерея").exists(timeout=3):
        d(text="Галерея").click()
    elif d(description="Галерея").exists(timeout=1):
        d(description="Галерея").click()
    elif d(descriptionContains="Фото/видео").exists(timeout=1):
        d(descriptionContains="Фото/видео").click()
    else:
        vf = get_vision_fallback(android_cfg)
        if not vf or not vf.click_goal(
            d,
            "Open gallery picker to attach photos to the Facebook group post.",
            labels=("Галерея", "Gallery", "Фото/видео"),
        ):
            raise RuntimeError("Кнопка «Галерея» не найдена в композере группы")
    human_pause(android_cfg, scale=0.9)
    dismiss_permissions(d)

    _open_discussion_gallery_album(
        d,
        android_cfg,
        album=album,
        object_id=object_id,
        min_tiles=min(2, max_images),
    )
    human_pause(android_cfg, scale=0.8)
    time.sleep(0.6)

    selected = _select_discussion_photos(
        d, android_cfg, max_images=max_images, object_id=object_id
    )
    clicked_next = False
    for _ in range(15):
        try:
            if d(description="Далее").exists(timeout=0.5):
                d(description="Далее").click()
                clicked_next = True
                break
            if d(text="Далее").exists(timeout=0.3):
                d(text="Далее").click()
                clicked_next = True
                break
        except Exception:
            pass
        time.sleep(0.3)
    if not clicked_next:
        vf = get_vision_fallback(android_cfg)
        if not vf or not vf.tap_next(d):
            raise RuntimeError("После выбора фото нет «Далее»")
    human_pause(android_cfg, scale=1.0)
    return selected


def _discussion_set_caption(d, caption: str, android_cfg: dict[str, Any]) -> None:
    if not caption:
        return
    opened = False
    for label in (
        "Расскажите об этих фото",
        "Создайте общедоступную публикацию",
        "Напишите что-нибудь",
        "Что у вас нового",
    ):
        if d(textContains=label).exists(timeout=0.8):
            d(textContains=label).click()
            opened = True
            break
    if not opened and d(descriptionContains="Название публикации").exists(timeout=0.8):
        d(descriptionContains="Название публикации").click()
        opened = True
    if not opened and d(className="android.widget.EditText").exists(timeout=1):
        d(className="android.widget.EditText").click()
        opened = True
    if not opened:
        raise RuntimeError("Не найдено поле текста поста")
    time.sleep(0.4)
    if d(className="android.widget.EditText").exists(timeout=2):
        d(className="android.widget.EditText").set_text(caption[:8000])
    else:
        d.send_keys(caption[:8000])
    time.sleep(0.4)
    if d(text="Готово").exists(timeout=2) or d(description="Готово").exists(timeout=0.5):
        (d(text="Готово") if d(text="Готово").exists() else d(description="Готово")).click()
        human_pause(android_cfg, scale=0.7)
    else:
        try:
            d.hide_keyboard()
        except Exception:
            pass


def _discussion_set_place(
    d,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
) -> str:
    mp = publisher_cfg.get("fb_marketplace") or {}
    fallback = (mp.get("address_fallback") or "Amphoe Thalang").strip()
    prefer = [
        f"{fallback}, Phuket, Thailand",
        f"{fallback}, Thailand",
        fallback,
        "Amphoe Thalang, Phuket, Thailand",
        "Thalang, Phuket, Thailand",
    ]

    if d(text="Место").exists(timeout=2):
        d(text="Место").click()
    elif d(descriptionContains="Местоположение").exists(timeout=1):
        d(descriptionContains="Местоположение").click()
    else:
        return ""
    human_pause(android_cfg, scale=0.8)

    # permission
    for label in (
        "Разрешить",
        "При использовании приложения",
        "Только в этот раз",
        "Allow",
        "While using the app",
    ):
        if d(text=label).exists(timeout=1.2):
            d(text=label).click()
            time.sleep(1.0)

    if d(className="android.widget.EditText").exists(timeout=3):
        d(className="android.widget.EditText").set_text(fallback)
    time.sleep(2.0)

    xml = d.dump_hierarchy()
    for target in prefer:
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            t = a.get("text") or ""
            if t != target:
                continue
            if "EditText" in (a.get("class") or ""):
                continue
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed or parsed[1] < 200:
                continue
            low = t.lower()
            if "hong kong" in low or "гонконг" in low:
                continue
            d.click((parsed[0] + parsed[2]) // 2, (parsed[1] + parsed[3]) // 2)
            human_pause(android_cfg, scale=0.8)
            _discussion_ensure_compose_ready(d, android_cfg)
            return t

    # любая подсказка с Thalang / Phuket без Hong Kong
    for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
        a = node.attrib
        t = a.get("text") or ""
        low = t.lower()
        if "EditText" in (a.get("class") or ""):
            continue
        if "hong kong" in low or "гонконг" in low:
            continue
        if "thalang" not in low and "phuket" not in low:
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed or parsed[1] < 200:
            continue
        d.click((parsed[0] + parsed[2]) // 2, (parsed[1] + parsed[3]) // 2)
        human_pause(android_cfg, scale=0.8)
        _discussion_ensure_compose_ready(d, android_cfg)
        return t
    raise RuntimeError(f"Не удалось выбрать место «{fallback}» для поста в группе")


def _discussion_publish_or_stop(
    d,
    *,
    confirm_post: bool,
    android_cfg: dict[str, Any],
    group_url: str,
    grp_cfg: dict[str, Any] | None = None,
) -> ChannelResult:
    stopped_note = (
        f"Discussion-пост готов (фото+текст+место), Опубликовать не нажат (--live). "
        f"group={group_url}"
    )
    success_note = f"Нажато «Опубликовать» в группу {group_url}"

    try:
        _discussion_ensure_compose_ready(d, android_cfg)
    except RuntimeError as exc:
        return ChannelResult(channel="fb_groups", ok=False, reason=str(exc))

    if not confirm_post:
        if _discussion_bottom_publish_visible(d):
            return ChannelResult(
                channel="fb_groups",
                ok=True,
                skipped=True,
                reason="stopped_before_publish",
                note=stopped_note,
            )
        return ChannelResult(
            channel="fb_groups",
            ok=False,
            reason="publish_button_not_found",
        )

    if not _discussion_wait_for_bottom_publish(d, android_cfg, timeout_sec=120):
        return ChannelResult(
            channel="fb_groups",
            ok=False,
            reason="publish_button_not_found",
        )
    if not _discussion_tap_bottom_publish(d, android_cfg):
        return ChannelResult(
            channel="fb_groups",
            ok=False,
            reason="publish_button_click_failed",
        )

    human_pause(android_cfg, scale=1.6)
    if _discussion_bottom_publish_visible(d):
        if not _discussion_tap_bottom_publish(d, android_cfg):
            return ChannelResult(
                channel="fb_groups",
                ok=False,
                reason="publish_button_still_visible",
            )
        human_pause(android_cfg, scale=1.2)
        if _discussion_bottom_publish_visible(d):
            return ChannelResult(
                channel="fb_groups",
                ok=False,
                reason="publish_button_still_visible",
            )
    if grp_cfg:
        _post_publish_pause(grp_cfg)
    return ChannelResult(channel="fb_groups", ok=True, note=success_note)


class FbGroupsChannel:
    name = "fb_groups"

    def publish(
        self,
        job: PublishJob,
        *,
        dry_run: bool,
        android_cfg: dict[str, Any],
        publisher_cfg: dict[str, Any],
        confirm_post: bool = False,
    ) -> ChannelResult:
        from ..browser.backend import channel_uses_browser

        if channel_uses_browser(self.name, publisher_cfg):
            from ..browser.publish import publish_fb_groups_browser

            bad = require_designed_carousel(job, self.name)
            if bad:
                return bad
            if dry_run:
                images = job.local_images or job.image_urls
                return ChannelResult(
                    channel=self.name,
                    ok=True,
                    skipped=True,
                    reason="dry-run",
                    note=(
                        f"FB Groups browser (agent7): groups={len(job.fb_groups)}; "
                        f"images={len(images)}; caption_len="
                        f"{len((job.caption_fb or job.caption_social or '').strip())}"
                    ),
                )
            return publish_fb_groups_browser(
                job,
                publisher_cfg=publisher_cfg,
                confirm_post=confirm_post,
            )

        bad = require_designed_carousel(job, self.name)
        if bad:
            return bad
        images = job.device_images or job.local_images or job.image_urls
        groups = job.fb_groups
        if not groups:
            return ChannelResult(
                channel=self.name,
                ok=True,
                skipped=True,
                reason="all groups already published for this object",
            )
        caption = (job.caption_fb or job.caption_social or "").strip()
        if not images and not caption:
            return ChannelResult(
                channel=self.name,
                ok=False,
                skipped=True,
                reason="no images/caption",
            )

        cfg = _grp_cfg(publisher_cfg, android_cfg)
        targets = list(groups)
        album = device_media_album(
            job.device_images,
            cfg.get("album") or ALBUM_DEFAULT,
        )
        max_images = min(
            len(images),
            int(
                cfg.get("max_images")
                or (publisher_cfg.get("media") or {}).get("max_groups_images")
                or 9
            ),
        )
        package = _package(android_cfg)
        note = (
            f"FB Groups: targets={targets}; images_available={len(images)}; "
            f"caption_len={len(caption)}"
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

        last: ChannelResult | None = None
        skipped_not_member: list[str] = []
        published_urls: list[str] = []
        state = load_state()
        try:
            d = connect_device(android_cfg)
            d.screen_on()
            for url in targets:
                try:
                    gid = resolve_group_id(url)
                    _open_group(d, package, gid, android_cfg)
                except RuntimeError as exc:
                    if "не участник" in str(exc).lower() or "заявка на вступление" in str(exc).lower():
                        skipped_not_member.append(url)
                        note = f"{note}; skipped_not_member={url}"
                        continue
                    raise
                _ensure_group_compose(d, android_cfg)
                mode = _detect_mode(d)
                note = f"{note}; group_id={gid}; mode={mode}"

                if mode == "property":
                    _open_property_form(d, android_cfg)
                    meta = mp._fill_form(
                        d,
                        job,
                        android_cfg,
                        publisher_cfg,
                        album=album,
                        max_images=max_images,
                    )
                    note = f"{note}; filled={meta}"
                    mp._go_to_publish_screen(d, android_cfg)
                    last = mp._publish_or_stop(
                        d,
                        confirm_post=confirm_post,
                        android_cfg=android_cfg,
                        channel=self.name,
                        post_publish_pause_seconds=cfg.get(
                            "post_publish_pause_seconds"
                        ),
                    )
                elif mode == "discussion":
                    _open_discussion_compose(d, android_cfg)
                    n = 0
                    if images:
                        n = _discussion_add_photos(
                            d,
                            android_cfg,
                            album=album,
                            object_id=job.object_id,
                            max_images=max_images,
                        )
                    _discussion_set_caption(d, caption, android_cfg)
                    place = _discussion_set_place(d, android_cfg, publisher_cfg)
                    note = f"{note}; photos={n}; place={place}"
                    last = _discussion_publish_or_stop(
                        d,
                        confirm_post=confirm_post,
                        android_cfg=android_cfg,
                        group_url=url,
                        grp_cfg=cfg,
                    )
                else:
                    return ChannelResult(
                        channel=self.name,
                        ok=False,
                        reason="unknown_group_ui",
                        note=note,
                    )
                last.note = f"{(last.note or '')}; {note}".strip("; ")
                if not last.ok:
                    last.published_group_urls = published_urls or None
                    return last
                if confirm_post and not dry_run:
                    mark_fb_group_published(
                        state,
                        job.object_id,
                        url,
                        note=last.note,
                    )
                    published_urls.append(url)
            if last is None:
                return ChannelResult(
                    channel=self.name,
                    ok=False,
                    reason="not_member_in_all_groups",
                    note=f"{note}; skipped={skipped_not_member}",
                    published_group_urls=published_urls or None,
                )
            if skipped_not_member:
                last.note = (
                    f"{(last.note or '')}; skipped_not_member={skipped_not_member}"
                ).strip("; ")
            if last:
                last = attach_post_url(d, last, self.name, android_cfg, confirm_post=confirm_post)
            if last:
                last.published_group_urls = published_urls or None
            return last or ChannelResult(
                channel=self.name, ok=False, reason="no_targets", note=note
            )
        except Exception as e:
            return ChannelResult(
                channel=self.name,
                ok=False,
                reason=str(e),
                note=note,
            )
