from __future__ import annotations

import re
import time
from typing import Any
from xml.etree import ElementTree as ET

from ..android.ui import (
    connect_device,
    dismiss_permissions,
    human_pause,
    pass_threads_human_check,
)
from ..models import PublishJob
from .base import ChannelResult
from .base import (
    bounds_center,
    carousel_grid_pick_order,
    device_media_album,
    gallery_selection_state,
    require_designed_carousel,
    select_carousel_photos_toggle_safe,
)
from ._post_url import attach_post_url

PKG_DEFAULT = "com.instagram.android"
ALBUM_DEFAULT = "Видео"


def _package(android_cfg: dict[str, Any]) -> str:
    return android_cfg.get("packages", {}).get("instagram", PKG_DEFAULT)


def _ig_cfg(publisher_cfg: dict[str, Any], android_cfg: dict[str, Any]) -> dict[str, Any]:
    base = dict(publisher_cfg.get("instagram") or {})
    base.update(android_cfg.get("instagram") or {})
    return base


def _parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _tap_bounds(d, bounds: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = bounds
    d.click((x1 + x2) // 2, (y1 + y2) // 2)


def _tap_text_bounds(d, text: str) -> bool:
    xml = d.dump_hierarchy()
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        if a.get("text") != text:
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        _tap_bounds(d, parsed)
        return True
    return False


def _build_caption(job: PublishJob) -> str:
    caption = job.caption_social or ""
    if job.cta_instagram:
        caption = f"{caption}\n\n{job.cta_instagram}".strip()
    return caption


def _open_instagram(d, package: str, android_cfg: dict[str, Any]) -> None:
    d.press("home")
    time.sleep(0.5)
    d.app_stop(package)
    time.sleep(0.8)
    d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    human_pause(android_cfg, scale=1.4)
    dismiss_permissions(d)
    _dismiss_overlays(d, package, android_cfg)
    for _ in range(20):
        if d.app_current().get("package") == package:
            return
        time.sleep(0.4)
    raise RuntimeError("Instagram не открылся")


def _dismiss_overlays(d, package: str, android_cfg: dict[str, Any]) -> None:
    """Промо, NUX, tip-оверлеи."""
    for _ in range(4):
        acted = False
        if d(text="Не сейчас").exists(timeout=0.6) or d(description="Не сейчас").exists(timeout=0.3):
            if d(text="Не сейчас").exists():
                d(text="Не сейчас").click()
            else:
                d(description="Не сейчас").click()
            human_pause(android_cfg, scale=0.4)
            acted = True
        if d(text="Начать новое видео").exists(timeout=0.5):
            d(text="Начать новое видео").click()
            human_pause(android_cfg, scale=0.5)
            acted = True
        cancel = f"{package}:id/clips_nux_sheet_cancel_button"
        if d(resourceId=cancel).exists(timeout=0.5):
            d(resourceId=cancel).click()
            human_pause(android_cfg, scale=0.4)
            acted = True
        if d(description="Продолжить").exists(timeout=0.4) and d(
            resourceId=f"{package}:id/clips_download_privacy_nux_button"
        ).exists(timeout=0.2):
            d(resourceId=f"{package}:id/clips_download_privacy_nux_button").click()
            human_pause(android_cfg, scale=0.4)
            acted = True
        if d(text="Продолжить").exists(timeout=0.4) and d(textContains="общедоступн").exists(
            timeout=0.2
        ):
            d(text="Продолжить").click()
            human_pause(android_cfg, scale=0.4)
            acted = True
        # sheet «Репост публикаций» / dimmer
        if d(text="Репост публикаций").exists(timeout=0.4):
            if d(text="ОК").exists(timeout=0.4):
                d(text="ОК").click()
            elif d(resourceId=f"{package}:id/background_dimmer").exists(timeout=0.3):
                d(resourceId=f"{package}:id/background_dimmer").click()
            else:
                d.press("back")
            human_pause(android_cfg, scale=0.4)
            acted = True
        if d(textContains="Коснитесь кнопки").exists(timeout=0.3):
            d.press("back")
            human_pause(android_cfg, scale=0.3)
            acted = True
        if not acted:
            break


def _dismiss_draft_prompt(d, android_cfg: dict[str, Any]) -> None:
    if d(text="Начать новое видео").exists(timeout=2.0):
        d(text="Начать новое видео").click()
        human_pause(android_cfg, scale=0.7)


def _set_caption_text(d, package: str, text: str) -> None:
    rid = f"{package}:id/caption_input_text_view"
    try:
        d.set_fastinput_ime(False)
    except Exception:
        pass
    if d(resourceId=rid).exists(timeout=2):
        el = d(resourceId=rid)
        el.click()
        try:
            el.set_text(text)
            return
        except Exception:
            pass
    elif d(textContains="подпись").exists(timeout=1):
        d(textContains="подпись").click()
    try:
        d.send_keys(text, clear=False)
    except Exception:
        escaped = text.replace(" ", "%s").replace("'", "")[:400]
        d.shell(f"input text {escaped}")


def _hide_keyboard_safe(d) -> None:
    try:
        d.hide_keyboard()
    except Exception:
        pass
    time.sleep(0.3)
    w, h = d.window_size()
    d.click(int(w * 0.5), int(h * 0.08))
    time.sleep(0.3)


def _location_already_set(d, package: str, aliases: list[str]) -> str | None:
    for rid in (f"{package}:id/venue_name", f"{package}:id/location_label"):
        if not d(resourceId=rid).exists(timeout=0.6):
            continue
        cur = (d(resourceId=rid).get_text() or "").strip()
        if not cur or cur == "Добавить место":
            continue
        if any(a.lower() in cur.lower() or cur.lower() in a.lower() for a in aliases):
            return cur
    return None


def _open_location_picker(d, package: str, android_cfg: dict[str, Any]) -> None:
    """Открыть экран «Выбрать место». Кликабельна только first_row, не desc «Добавить место»."""
    _dismiss_overlays(d, package, android_cfg)
    if d(text="ОК").exists(timeout=0.5):
        d(text="ОК").click()
        human_pause(android_cfg, scale=0.35)

    search_rid = f"{package}:id/row_search_edit_text"
    first_row = f"{package}:id/metadata_location_first_row"
    location_row = f"{package}:id/metadata_location_row"

    def _picker_open() -> bool:
        return bool(
            d(resourceId=search_rid).exists(timeout=0.8)
            or d(text="Поиск места…").exists(timeout=0.4)
            or d(textContains="Выбрать место").exists(timeout=0.4)
        )

    w, h = d.window_size()
    for attempt in range(4):
        if _picker_open():
            return
        if attempt:
            d.swipe(w // 2, int(h * 0.65), w // 2, int(h * 0.35), 0.3)
            human_pause(android_cfg, scale=0.3)

        if d(resourceId=first_row).exists(timeout=1.2):
            d(resourceId=first_row).click()
        elif d(resourceId=location_row).exists(timeout=0.6):
            info = d(resourceId=location_row).info
            b = info.get("bounds") or {}
            # верхняя половина ряда (сама кнопка локации), не education tip ниже
            d.click((b["left"] + b["right"]) // 2, b["top"] + max(20, (b["bottom"] - b["top"]) // 4))
        elif d(text="Добавить место").exists(timeout=0.6):
            info = d(text="Добавить место").info
            b = info.get("bounds") or {}
            d.click((b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2)
        else:
            continue
        human_pause(android_cfg, scale=0.9)
        if _picker_open():
            return

    raise RuntimeError("Обязательно: «Добавить место» не найдено")


def _click_location_result(d, aliases: list[str]) -> str | None:
    """Клик по Button с content-desc локации (не по EditText поиска)."""
    xml = d.dump_hierarchy()
    buttons: list[tuple[int, str, tuple[int, int, int, int]]] = []
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        if a.get("clickable") != "true":
            continue
        cls = a.get("class") or ""
        if "EditText" in cls:
            continue
        desc = (a.get("content-desc") or "").strip()
        text = (a.get("text") or "").strip()
        label = desc or text
        if not label:
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed or parsed[1] < 400:
            continue
        for i, name in enumerate(aliases):
            if label == name or name.lower() in label.lower():
                buttons.append((i, name if label == name else label, parsed))
                break
    if not buttons:
        return None
    buttons.sort(key=lambda x: x[0])
    _, chosen, bounds = buttons[0]
    _tap_bounds(d, bounds)
    return chosen


def _select_location(
    d,
    package: str,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
) -> str:
    cfg = _ig_cfg(publisher_cfg, android_cfg)
    location = cfg.get("location") or "Phuket, Thailand"
    aliases = list(
        cfg.get("location_aliases")
        or ["Phuket, Thailand", "Phuket Island, Thailand", "Phuket", "Пхукет"]
    )
    if location not in aliases:
        aliases.insert(0, location)

    already = _location_already_set(d, package, aliases)
    if already:
        return already

    _open_location_picker(d, package, android_cfg)

    query = "Phuket"
    search_rid = f"{package}:id/row_search_edit_text"
    if d(resourceId=search_rid).exists(timeout=3):
        d(resourceId=search_rid).click()
        d.send_keys(query, clear=True)
    elif d(className="android.widget.EditText").exists(timeout=2):
        d(className="android.widget.EditText").click()
        d.send_keys(query, clear=True)
    human_pause(android_cfg, scale=1.2)

    chosen = _click_location_result(d, aliases)
    if not chosen:
        # fallback: description на Button
        for name in aliases:
            if d(description=name).exists(timeout=1.5):
                d(description=name).click()
                chosen = name
                break
    if not chosen:
        raise RuntimeError(f"Обязательно: локация «{location}» / Пхукет не найдена")

    human_pause(android_cfg, scale=0.7)
    if d(text="Добавить").exists(timeout=2):
        d(text="Добавить").click()
        human_pause(android_cfg, scale=0.8)

    applied = _location_already_set(d, package, aliases)
    if applied:
        return applied
    if any(d(textContains=a).exists(timeout=0.5) for a in ("Phuket", "Пхукет")):
        return chosen
    raise RuntimeError("Локация Пхукет не применилась на экране публикации")


def _scroll_share_options(d, android_cfg: dict[str, Any]) -> None:
    w, h = d.window_size()
    for _ in range(5):
        if d(text="Threads").exists(timeout=0.4) or d(textContains="Поделиться также").exists(
            timeout=0.3
        ):
            return
        if d(text="OpenHome").exists(timeout=0.3) or d(textContains="Facebook").exists(timeout=0.3):
            return
        if d(textContains="Ваша история").exists(timeout=0.3) or d(textContains="история").exists(
            timeout=0.3
        ):
            return
        d.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.38), 0.35)
        human_pause(android_cfg, scale=0.35)


def _on_instagram_compose(d, package: str) -> bool:
    if d.app_current().get("package") != package:
        return False
    return bool(
        d(resourceId=f"{package}:id/caption_input_text_view").exists(timeout=0.4)
        or d(text="Поделиться также в...").exists(timeout=0.3)
        or d(textContains="Поделиться также").exists(timeout=0.3)
        or d(resourceId=f"{package}:id/location_label").exists(timeout=0.3)
        or d(text="Новое видео Reels").exists(timeout=0.3)
    )


def _return_to_instagram_compose(
    d, package: str, android_cfg: dict[str, Any], *, attempts: int = 8
) -> None:
    """После тоггла Threads/FB иногда открывается Barcelona challenge — вернуться в IG."""
    threads_pkg = "com.instagram.barcelona"
    for _ in range(attempts):
        cur = d.app_current().get("package") or ""
        if cur == package and _on_instagram_compose(d, package):
            return
        # challenge: один тап «Продолжить», затем назад в compose IG
        if cur == threads_pkg or d(textContains="подтвердите, что вы").exists(timeout=0.3):
            if pass_threads_human_check(d, timeout=0.8):
                human_pause(android_cfg, scale=0.5)
            d.press("back")
            human_pause(android_cfg, scale=0.4)
            if (d.app_current() or {}).get("package") != package:
                d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
                human_pause(android_cfg, scale=0.7)
            continue
        if cur != package:
            d.press("back")
            human_pause(android_cfg, scale=0.45)
            continue
        if d(text="Отмена").exists(timeout=0.3):
            d(text="Отмена").click()
            human_pause(android_cfg, scale=0.35)
            continue
        d.press("back")
        human_pause(android_cfg, scale=0.4)
    if not _on_instagram_compose(d, package):
        raise RuntimeError(
            "Ушли из экрана публикации IG (часто challenge Threads). "
            "Нажмите «Продолжить» в Threads и повторите publish."
        )


def _turn_on_toggle_near(
    d, package: str, labels: list[str], android_cfg: dict[str, Any]
) -> bool:
    """Включить ToggleButton возле текста (Threads / OpenHome / Facebook)."""
    xml = d.dump_hierarchy()
    label_ys: list[tuple[int, str]] = []
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        t = a.get("text") or ""
        if not t:
            continue
        if any(lbl.lower() in t.lower() for lbl in labels):
            parsed = _parse_bounds(a.get("bounds", ""))
            if parsed:
                label_ys.append(((parsed[1] + parsed[3]) // 2, t))
    if not label_ys:
        return False

    toggles: list[tuple[tuple[int, int, int, int], bool]] = []
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        rid = a.get("resource-id") or ""
        cls = a.get("class") or ""
        is_toggle = rid.endswith("/toggle") or any(
            x in cls for x in ("Switch", "Toggle", "CheckBox", "CompoundButton")
        )
        if not is_toggle:
            continue
        if a.get("clickable") != "true" and a.get("checkable") != "true":
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        # переключатели справа
        if parsed[0] < 400:
            continue
        checked = a.get("checked") == "true" or a.get("selected") == "true"
        toggles.append((parsed, checked))

    for ly, _name in label_ys:
        best = None
        best_dist = 10**9
        for bounds, checked in toggles:
            cy = (bounds[1] + bounds[3]) // 2
            dist = abs(cy - ly)
            if dist < best_dist:
                best_dist = dist
                best = (bounds, checked)
        if best and best_dist < 180:
            bounds, checked = best
            if not checked:
                _tap_bounds(d, bounds)
                time.sleep(0.8)
                _return_to_instagram_compose(d, package, android_cfg)
            return True
        # fallback: тап справа от текста строки
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            t = a.get("text") or ""
            if t != _name:
                continue
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed:
                continue
            w, _h = d.window_size()
            d.click(int(w * 0.88), (parsed[1] + parsed[3]) // 2)
            time.sleep(0.8)
            _return_to_instagram_compose(d, package, android_cfg)
            return True
    return False


def _enable_crosspost_and_public(
    d,
    package: str,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
) -> str:
    """Threads + Facebook ON, аудитория общедоступная."""
    cfg = _ig_cfg(publisher_cfg, android_cfg)
    want_threads = bool(cfg.get("share_to_threads", True))
    want_facebook = bool(cfg.get("share_to_facebook", True))
    notes: list[str] = []

    _dismiss_overlays(d, package, android_cfg)
    _scroll_share_options(d, android_cfg)

    # Аудитория = Все (уже часто стоит)
    if d(text="Аудитория").exists(timeout=1):
        if d(text="Все").exists(timeout=0.5) or d(textContains="Общедоступн").exists(timeout=0.5):
            notes.append("audience=Все/общедоступный")
        else:
            _tap_text_bounds(d, "Аудитория")
            human_pause(android_cfg, scale=0.6)
            for label in ("Все", "Общедоступный", "Everyone", "Public"):
                if d(text=label).exists(timeout=1.5):
                    _tap_text_bounds(d, label)
                    notes.append(f"audience={label}")
                    human_pause(android_cfg, scale=0.6)
                    break
            # закрыть sheet если остался
            if d(resourceId=f"{package}:id/background_dimmer").exists(timeout=0.5):
                d.press("back")
                time.sleep(0.4)

    if want_threads:
        ok = _turn_on_toggle_near(d, package, ["Threads"], android_cfg)
        notes.append("threads=on" if ok else "threads=not_found")
        human_pause(android_cfg, scale=0.4)

    if want_facebook:
        ok = _turn_on_toggle_near(d, package, ["OpenHome", "Facebook"], android_cfg)
        notes.append("facebook=on" if ok else "facebook=not_found")
        human_pause(android_cfg, scale=0.4)

    _return_to_instagram_compose(d, package, android_cfg)

    # Подтверждение подписи Facebook · Общедоступный
    if d(textContains="Facebook · Общедоступный").exists(timeout=0.8):
        notes.append("facebook_subtitle=общедоступный")

    return "; ".join(notes) if notes else "crosspost=skipped"


# ─── Reel (линейный 11-шаговый флоу) ─────────────────────────────────────────


def _click_dalee(d, package: str, android_cfg: dict[str, Any]) -> bool:
    from ..android.ui import click_text_or_desc

    if click_text_or_desc(d, "Далее", timeout=1.5) or click_text_or_desc(d, "Next", timeout=0.5):
        human_pause(android_cfg, scale=0.8)
        return True
    for rid in (
        f"{package}:id/clips_right_action_button",
        f"{package}:id/next_button_textview",
        f"{package}:id/media_thumbnail_tray_button",
        f"{package}:id/share_button",
    ):
        if rid.endswith("share_button"):
            continue
        if d(resourceId=rid).exists(timeout=0.4):
            d(resourceId=rid).click()
            human_pause(android_cfg, scale=0.8)
            return True
    return False


def step1_open_instagram(d, package: str, android_cfg: dict[str, Any]) -> None:
    """1. Открыть Instagram"""
    _open_instagram(d, package, android_cfg)
    if d(description="Дом").exists(timeout=2):
        d(description="Дом").click()
        human_pause(android_cfg, scale=0.6)
    _dismiss_overlays(d, package, android_cfg)


def step2_press_plus(d, package: str, android_cfg: dict[str, Any]) -> None:
    """2. Нажать + в верхнем левом углу"""
    _dismiss_overlays(d, package, android_cfg)
    w, h = d.window_size()
    d.click(int(w * 0.06), int(h * 0.07))
    human_pause(android_cfg, scale=1.0)
    dismiss_permissions(d)
    _dismiss_overlays(d, package, android_cfg)

    # если открылся выбор режима — REELS
    for label in ("REELS", "Reels", "Клип", "VIDEO"):
        if d(description=label).exists(timeout=1.2):
            d(description=label).click()
            human_pause(android_cfg, scale=0.5)
            break
        if d(text=label).exists(timeout=0.5):
            _tap_text_bounds(d, label)
            human_pause(android_cfg, scale=0.5)
            break
    if d(resourceId=f"{package}:id/cam_dest_clips").exists(timeout=1.5):
        d(resourceId=f"{package}:id/cam_dest_clips").click()
        human_pause(android_cfg, scale=0.4)


def step3_dismiss_draft(d, android_cfg: dict[str, Any]) -> None:
    """3. «Продолжить редактирование черновика?» → Начать новое видео"""
    for _ in range(4):
        if d(text="Начать новое видео").exists(timeout=1.5):
            d(text="Начать новое видео").click()
            human_pause(android_cfg, scale=0.7)
            return
        if d(textContains="черновик").exists(timeout=0.5) or d(
            textContains="Продолжить редактирование"
        ).exists(timeout=0.4):
            if d(text="Начать новое видео").exists(timeout=1):
                d(text="Начать новое видео").click()
                human_pause(android_cfg, scale=0.7)
                return
        time.sleep(0.3)


def step4_select_latest_video(
    d, package: str, android_cfg: dict[str, Any], album: str
) -> None:
    """4. Последнее (самое новое) видео из галереи"""
    # открыть галерею с камеры Reels
    rid = f"{package}:id/gallery_preview_button"
    if d(resourceId=rid).exists(timeout=3) or d(description="Галерея").exists(timeout=1):
        if d(resourceId=rid).exists(timeout=0.5):
            d(resourceId=rid).click()
        else:
            d(description="Галерея").click()
        human_pause(android_cfg, scale=0.8)
        dismiss_permissions(d)

    # Системная коллекция «Видео»: загруженный ролик после media scan — последний.
    if d(text="Недавние").exists(timeout=1.5) or d(
        resourceId=f"{package}:id/gallery_folder_menu_tv"
    ).exists(timeout=0.8):
        if d(text="Недавние").exists(timeout=0.5):
            d(text="Недавние").click()
        else:
            d(resourceId=f"{package}:id/gallery_folder_menu_tv").click()
        human_pause(android_cfg, scale=0.5)
        if d(text=album).exists(timeout=2.5):
            d(text=album).click()
            human_pause(android_cfg, scale=0.7)
        elif d(text="Видео").exists(timeout=1):
            _tap_text_bounds(d, "Видео")
            human_pause(android_cfg, scale=0.6)

    # Первая ячейка списка = последнее/самое новое видео.
    candidates: list[tuple[int, int, int, int]] = []
    xml = d.dump_hierarchy()
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        if a.get("clickable") != "true":
            continue
        r = a.get("resource-id") or ""
        c = (a.get("content-desc") or "").lower()
        if not r.endswith("gallery_grid_item_thumbnail"):
            continue
        if "видео" not in c and "video" not in c:
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        if y1 < 150 or (x2 - x1) < 80:
            continue
        candidates.append((y1, x1, (x1 + x2) // 2, (y1 + y2) // 2))
    if not candidates:
        # fallback: любой верхний thumb
        for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
            a = node.attrib
            r = a.get("resource-id") or ""
            if a.get("clickable") == "true" and r.endswith("gallery_grid_item_thumbnail"):
                parsed = _parse_bounds(a.get("bounds", ""))
                if parsed and parsed[1] > 150:
                    x1, y1, x2, y2 = parsed
                    candidates.append((y1, x1, (x1 + x2) // 2, (y1 + y2) // 2))
    if not candidates:
        raise RuntimeError("Шаг 4: видео в галерее не найдено")
    candidates.sort()
    d.click(candidates[0][2], candidates[0][3])
    human_pause(android_cfg, scale=1.0)


def step5_press_dalee_after_video(
    d, package: str, android_cfg: dict[str, Any]
) -> None:
    """5. Видео выбрано → Далее"""
    for _ in range(15):
        if d(resourceId=f"{package}:id/caption_input_text_view").exists(timeout=0.4):
            return
        if d(textContains="Добавить подпись").exists(timeout=0.3):
            return
        if _click_dalee(d, package, android_cfg):
            continue
        time.sleep(0.35)
    if not (
        d(resourceId=f"{package}:id/caption_input_text_view").exists(timeout=2)
        or d(textContains="Добавить подпись").exists(timeout=1)
    ):
        raise RuntimeError("Шаг 5: экран подписи не открылся после «Далее»")


def step6_set_caption(
    d, package: str, caption: str, android_cfg: dict[str, Any]
) -> None:
    """6. Добавляем подпись"""
    if d(resourceId=f"{package}:id/caption_input_text_view").exists(timeout=3):
        d(resourceId=f"{package}:id/caption_input_text_view").click()
    _set_caption_text(d, package, caption[:2100])
    _hide_keyboard_safe(d)
    human_pause(android_cfg, scale=0.4)


def step7_set_location(
    d,
    package: str,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
) -> str:
    """7. Добавить место → Пхукет"""
    return _select_location(d, package, android_cfg, publisher_cfg)


def step8_share_also(
    d,
    package: str,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
) -> str:
    """8. Поделиться также: Threads, OpenHome FB, Ваша история"""
    cfg = _ig_cfg(publisher_cfg, android_cfg)
    notes: list[str] = []
    _dismiss_overlays(d, package, android_cfg)
    _scroll_share_options(d, android_cfg)

    # дополнительно скролл к «Ваша история»
    w, h = d.window_size()
    for _ in range(3):
        if (
            d(text="Threads").exists(timeout=0.3)
            or d(textContains="Ваша история").exists(timeout=0.3)
            or d(textContains="история").exists(timeout=0.3)
        ):
            break
        d.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.4), 0.3)
        time.sleep(0.35)

    if cfg.get("share_to_threads", True):
        ok = _turn_on_toggle_near(d, package, ["Threads"], android_cfg)
        notes.append("threads=on" if ok else "threads=not_found")
        _return_to_instagram_compose(d, package, android_cfg)
        _scroll_share_options(d, android_cfg)

    if cfg.get("share_to_facebook", True):
        ok = _turn_on_toggle_near(d, package, ["OpenHome", "Facebook"], android_cfg)
        notes.append("facebook=on" if ok else "facebook=not_found")
        _return_to_instagram_compose(d, package, android_cfg)
        _scroll_share_options(d, android_cfg)

    if cfg.get("share_to_story", True):
        w, h = d.window_size()
        for _ in range(2):
            if d(textContains="Ваша история").exists(timeout=0.4) or d(
                textContains="история"
            ).exists(timeout=0.3):
                break
            d.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.4), 0.3)
            time.sleep(0.35)
        ok = _turn_on_toggle_near(
            d,
            package,
            ["Ваша история", "Your story", "История", "Story"],
            android_cfg,
        )
        notes.append("story=on" if ok else "story=not_found")
    _return_to_instagram_compose(d, package, android_cfg)
    return "; ".join(notes) if notes else "crosspost=none"


def step9_press_dalee_before_share(
    d, package: str, android_cfg: dict[str, Any]
) -> None:
    """9. Далее (перед финальным Поделиться)"""
    _return_to_instagram_compose(d, package, android_cfg)
    _dismiss_overlays(d, package, android_cfg)
    # если уже видна кнопка Поделиться на финале — ок
    if d(resourceId=f"{package}:id/share_button").exists(timeout=1) and not d(
        resourceId=f"{package}:id/caption_input_text_view"
    ).exists(timeout=0.3):
        return
    if not _click_dalee(d, package, android_cfg):
        # иногда share_button на compose и есть «Далее» рядом
        if d(resourceId=f"{package}:id/share_button").exists(timeout=1):
            # на экране compose «Поделиться» может называться иначе — ждём шаг 10
            return
        raise RuntimeError("Шаг 9: кнопка «Далее» не найдена")
    human_pause(android_cfg, scale=0.8)


def step10_share(
    d, package: str, *, confirm_post: bool, android_cfg: dict[str, Any]
) -> ChannelResult:
    """10. Поделиться"""
    _dismiss_overlays(d, package, android_cfg)
    if not confirm_post:
        if d(resourceId=f"{package}:id/save_draft_button").exists(timeout=2):
            d(resourceId=f"{package}:id/save_draft_button").click()
            human_pause(android_cfg)
        return ChannelResult(
            channel="instagram_reel",
            ok=True,
            skipped=True,
            reason="stopped_before_publish",
            note="Reel готов — «Поделиться» не нажата (--live)",
        )

    shared = False
    if d(resourceId=f"{package}:id/share_button").exists(timeout=3):
        d(resourceId=f"{package}:id/share_button").click()
        shared = True
    elif d(description="Поделиться").exists(timeout=1.5):
        d(description="Поделиться").click()
        shared = True
    elif d(text="Поделиться").exists(timeout=1):
        d(text="Поделиться").click()
        shared = True
    if not shared:
        return ChannelResult(
            channel="instagram_reel", ok=False, reason="share_button_not_found"
        )
    human_pause(android_cfg, scale=1.0)

    nux = f"{package}:id/clips_nux_sheet_share_button"
    if d(resourceId=nux).exists(timeout=3):
        d(resourceId=nux).click()
        human_pause(android_cfg, scale=0.8)
    elif d(description="Поделиться").exists(timeout=1.5):
        d(description="Поделиться").click()
        human_pause(android_cfg, scale=0.8)
    return ChannelResult(
        channel="instagram_reel",
        ok=True,
        note="Reel: нажато Поделиться",
    )


class InstagramReelChannel:
    name = "instagram_reel"

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
        package = _package(android_cfg)
        cfg = _ig_cfg(publisher_cfg, android_cfg)
        album = cfg.get("video_album") or cfg.get("album") or ALBUM_DEFAULT
        caption = _build_caption(job)
        note = f"IG Reel: caption_len={len(caption)}; album={album}"
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
            d = connect_device(android_cfg)
            d.screen_on()
            step1_open_instagram(d, package, android_cfg)
            step2_press_plus(d, package, android_cfg)
            step3_dismiss_draft(d, android_cfg)
            step4_select_latest_video(d, package, android_cfg, album)
            step5_press_dalee_after_video(d, package, android_cfg)
            step6_set_caption(d, package, caption, android_cfg)
            loc = step7_set_location(d, package, android_cfg, publisher_cfg)
            # «Поделиться также» (Threads/FB/Story) — вручную в настройках IG, не трогаем
            step9_press_dalee_before_share(d, package, android_cfg)
            note = f"{note}; location={loc}; crosspost=defaults"
            result = step10_share(
                d, package, confirm_post=confirm_post, android_cfg=android_cfg
            )
            # после Share с Threads часто вылетает challenge — «Продолжить»
            try:
                if pass_threads_human_check(d, timeout=2.0):
                    human_pause(android_cfg, scale=0.5)
                if (d.app_current() or {}).get("package") != package:
                    d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
                    human_pause(android_cfg, scale=0.7)
            except Exception:
                pass
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
            return ChannelResult(channel=self.name, ok=False, reason=str(e), note=note)


# ─── Carousel ───────────────────────────────────────────────────────────────


def _open_new_post(d, package: str, android_cfg: dict[str, Any]) -> None:
    _dismiss_overlays(d, package, android_cfg)
    if d(description="Дом").exists(timeout=3):
        d(description="Дом").click()
        human_pause(android_cfg, scale=0.7)
    _dismiss_overlays(d, package, android_cfg)
    # Верхний левый create на ленте
    w, h = d.window_size()
    d.click(int(w * 0.06), int(h * 0.07))
    human_pause(android_cfg, scale=1.0)
    _dismiss_overlays(d, package, android_cfg)
    # Режим ПУБЛИКАЦИЯ
    if d(description="ПУБЛИКАЦИЯ").exists(timeout=4):
        d(description="ПУБЛИКАЦИЯ").click()
        human_pause(android_cfg, scale=0.5)
    elif d(text="ПУБЛИКАЦИЯ").exists(timeout=1):
        _tap_text_bounds(d, "ПУБЛИКАЦИЯ")
        human_pause(android_cfg, scale=0.5)
    if not (
        d(text="Новая публикация").exists(timeout=2)
        or d(description="ПУБЛИКАЦИЯ").exists(timeout=1)
        or d(resourceId=f"{package}:id/cam_dest_feed").exists(timeout=1)
    ):
        raise RuntimeError("Экран «Новая публикация» не открылся")


def _collect_unselected_ig_carousel(
    xml: str,
    *,
    package: str,
) -> list[tuple[int, int, int, int]]:
    cells: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        desc = a.get("content-desc") or ""
        rid = a.get("resource-id") or ""
        if a.get("clickable") != "true":
            continue
        if not rid.endswith("gallery_grid_item_thumbnail"):
            continue
        low = desc.lower()
        if "фото" not in low and "photo" not in low:
            continue
        if gallery_selection_state(desc) == "selected":
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        y1, x1, cx, cy = bounds_center(parsed)
        key = (cx, cy)
        if key in seen:
            continue
        seen.add(key)
        cells.append((y1, x1, cx, cy))
    return carousel_grid_pick_order(cells)


def _select_carousel_photos(
    d,
    package: str,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
    album_override: str | None = None,
) -> int:
    cfg = _ig_cfg(publisher_cfg, android_cfg)
    album = (
        album_override
        or cfg.get("carousel_album")
        or cfg.get("album")
        or "publisher_social"
    )
    max_n = int((cfg.get("carousel") or {}).get("max_images") or 10)
    max_n = max(2, min(max_n, 10))

    # multi-select
    if d(descriptionContains="Выбрать несколько").exists(timeout=3):
        d(descriptionContains="Выбрать несколько").click()
        human_pause(android_cfg, scale=0.5)

    # album
    if d(text="Недавние").exists(timeout=1) or d(resourceId=f"{package}:id/gallery_folder_menu_tv").exists(
        timeout=1
    ):
        if d(text="Недавние").exists(timeout=0.5):
            d(text="Недавние").click()
        else:
            d(resourceId=f"{package}:id/gallery_folder_menu_tv").click()
        human_pause(android_cfg, scale=0.5)
        if d(text=album).exists(timeout=3):
            d(text=album).click()
            human_pause(android_cfg, scale=0.7)
        else:
            raise RuntimeError(f"Альбом «{album}» не найден для карусели")

    selected = select_carousel_photos_toggle_safe(
        d,
        max_images=max_n,
        collect_unselected=lambda xml: _collect_unselected_ig_carousel(
            xml, package=package
        ),
        pause_s=0.35,
        min_selected=2,
    )
    return selected


def _advance_carousel_to_compose(d, package: str, android_cfg: dict[str, Any]) -> None:
    for stage in range(3):
        if d(resourceId=f"{package}:id/caption_input_text_view").exists(timeout=1):
            return
        if d(textContains="Добавить подпись").exists(timeout=0.5):
            return
        if d(resourceId=f"{package}:id/next_button_textview").exists(timeout=2):
            d(resourceId=f"{package}:id/next_button_textview").click()
            human_pause(android_cfg, scale=1.1)
            continue
        if d(description="Далее").exists(timeout=1):
            d(description="Далее").click()
            human_pause(android_cfg, scale=1.1)
            continue
        if d(text="Далее").exists(timeout=0.5):
            _tap_text_bounds(d, "Далее")
            human_pause(android_cfg, scale=1.1)
            continue
        time.sleep(0.5)
    if not (
        d(resourceId=f"{package}:id/caption_input_text_view").exists(timeout=2)
        or d(textContains="Добавить подпись").exists(timeout=1)
    ):
        raise RuntimeError("Экран подписи карусели не открылся")


def _share_or_stop_carousel(
    d, package: str, *, confirm_post: bool, android_cfg: dict[str, Any]
) -> ChannelResult:
    _dismiss_overlays(d, package, android_cfg)
    if not confirm_post:
        return ChannelResult(
            channel="instagram_carousel",
            ok=True,
            skipped=True,
            reason="stopped_before_publish",
            note="Carousel: caption+Phuket готовы, Share не нажат (--live для поста)",
        )
    if d(resourceId=f"{package}:id/share_footer_button").exists(timeout=3):
        d(resourceId=f"{package}:id/share_footer_button").click()
    elif d(description="Поделиться").exists(timeout=2):
        d(description="Поделиться").click()
    elif d(text="Поделиться").exists(timeout=1):
        d(text="Поделиться").click()
    else:
        return ChannelResult(
            channel="instagram_carousel", ok=False, reason="share_button_not_found"
        )
    human_pause(android_cfg, scale=1.5)
    return ChannelResult(
        channel="instagram_carousel",
        ok=True,
        note="Carousel: нажато Поделиться — дождитесь загрузки",
    )


class InstagramCarouselChannel:
    name = "instagram_carousel"

    def publish(
        self,
        job: PublishJob,
        *,
        dry_run: bool,
        android_cfg: dict[str, Any],
        publisher_cfg: dict[str, Any],
        confirm_post: bool = False,
    ) -> ChannelResult:
        bad = require_designed_carousel(job, self.name)
        if bad:
            return bad
        images = job.device_images or job.local_images or job.image_urls
        package = _package(android_cfg)
        caption = _build_caption(job)
        note = (
            f"IG Carousel: images_available={len(images)}; "
            f"source={job.images_source}; caption_len={len(caption)}"
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
            d = connect_device(android_cfg)
            d.screen_on()
            _open_instagram(d, package, android_cfg)
            _open_new_post(d, package, android_cfg)
            album = device_media_album(
                job.device_images,
                "publisher_social",
            )
            n = _select_carousel_photos(
                d,
                package,
                android_cfg,
                publisher_cfg,
                album_override=album,
            )
            _advance_carousel_to_compose(d, package, android_cfg)
            _dismiss_overlays(d, package, android_cfg)
            # caption
            if d(resourceId=f"{package}:id/caption_input_text_view").exists(timeout=3):
                d(resourceId=f"{package}:id/caption_input_text_view").click()
            _set_caption_text(d, package, caption[:2100])
            _hide_keyboard_safe(d)
            loc = _select_location(d, package, android_cfg, publisher_cfg)
            # «Поделиться также» — вручную в настройках IG, не трогаем
            note = f"{note}; selected={n}; location={loc}; crosspost=defaults"
            result = _share_or_stop_carousel(
                d, package, confirm_post=confirm_post, android_cfg=android_cfg
            )
            result = attach_post_url(d, result, self.name, android_cfg, confirm_post=confirm_post)
            result.note = f"{(result.note or '')}; {note}".strip("; ")
            return result
        except Exception as e:
            return ChannelResult(channel=self.name, ok=False, reason=str(e), note=note)
