from __future__ import annotations

import re
import time
from typing import Any
from xml.etree import ElementTree as ET

from ..android.ui import (
    click_center,
    click_text_or_desc,
    connect_device,
    dismiss_permissions,
    human_pause,
)
from ..models import PublishJob
from .base import ChannelResult
from .base import device_media_album, require_designed_carousel, carousel_bounds_pick_order
from ._post_url import attach_post_url

PKG_DEFAULT = "com.ss.android.ugc.trill"
ALBUM_NAME = "publisher_social"


def _parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _open_tiktok(d, package: str, android_cfg: dict[str, Any]) -> None:
    d.press("home")
    time.sleep(0.8)
    d.app_stop(package)
    time.sleep(0.8)
    d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    human_pause(android_cfg, scale=1.2)
    # Дождаться нижней панели
    for _ in range(15):
        if d(description="Создать").exists(timeout=1) or d(description="Create").exists(timeout=0.2):
            return
        time.sleep(1)
    raise RuntimeError("TikTok home: кнопка «Создать» не найдена (войдите в аккаунт?)")


def _tap_create(d, android_cfg: dict[str, Any]) -> None:
    if not click_text_or_desc(d, "Создать", timeout=3):
        if not click_text_or_desc(d, "Create", timeout=1):
            raise RuntimeError("Не удалось нажать «Создать»")
    human_pause(android_cfg)
    dismiss_permissions(d)


def _tap_upload(d, package: str, android_cfg: dict[str, Any]) -> None:
    rid = f"{package}:id/upload_hot_area"
    if d(resourceId=rid).exists(timeout=5):
        d(resourceId=rid).click()
    else:
        # fallback: левый нижний угол зоны загрузки (экран 720x1600)
        w, h = d.window_size()
        d.click(int(w * 0.1), int(h * 0.9))
    human_pause(android_cfg)
    dismiss_permissions(d)
    # Галерея открыта, если есть «Недавнее» / «Далее»
    if not (
        d(text="Недавнее").exists(timeout=4)
        or d(text="Recent").exists(timeout=0.5)
        or d(text="Далее").exists(timeout=0.5)
        or d(text="Next").exists(timeout=0.5)
    ):
        raise RuntimeError("Галерея TikTok не открылась после Upload")


def _select_album(d, android_cfg: dict[str, Any], album: str = ALBUM_NAME) -> None:
    if d(text=album).exists(timeout=1):
        d(text=album).click()
        human_pause(android_cfg, scale=0.7)
        return
    # Открыть список альбомов
    if d(text="Недавнее").exists(timeout=2):
        d(text="Недавнее").click()
    elif d(text="Recent").exists(timeout=1):
        d(text="Recent").click()
    else:
        # title area
        if d(resourceIdMatches=r".*:id/tv_title").exists(timeout=1):
            d(resourceIdMatches=r".*:id/tv_title").click()
    human_pause(android_cfg, scale=0.6)
    if d(text=album).exists(timeout=4):
        d(text=album).click()
        human_pause(android_cfg, scale=0.7)
        return
    raise RuntimeError(
        f"Альбом «{album}» не найден. Сначала: prepare --push-media"
    )


def _select_latest_video(d, android_cfg: dict[str, Any]) -> None:
    """Выбрать последнее/самое новое видео — первую ячейку списка «Видео»."""
    if d(description="Видео").exists(timeout=2):
        d(description="Видео").click()
        time.sleep(0.8)
    elif d(text="Видео").exists(timeout=0.5):
        # text may be inside non-clickable; try parent via description already handled
        pass

    xml = d.dump_hierarchy()
    candidates: list[tuple[int, int, str]] = []
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        if a.get("clickable") != "true":
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        # сетка галереи
        if y1 >= 220 and y2 <= 1380 and (x2 - x1) > 100 and (y2 - y1) > 100:
            # исключить большую кнопку записи по центру
            rid = a.get("resource-id", "")
            if rid.endswith("/suh"):
                continue
            candidates.append((y1, x1, a["bounds"]))
    candidates.sort()
    if not candidates:
        raise RuntimeError("В альбоме нет выбираемого видео")
    x1, y1, x2, y2 = _parse_bounds(candidates[0][2])  # type: ignore[misc]
    d.click((x1 + x2) // 2, (y1 + y2) // 2)
    human_pause(android_cfg, scale=0.8)


def _tap_next(d, package: str, android_cfg: dict[str, Any], *, stage: str) -> None:
    # После выбора медиа кнопка становится clickable
    for _ in range(20):
        for label in ("Далее", "Next"):
            matches = d(textStartsWith=label)
            if not matches.exists(timeout=0.25):
                continue
            for index in range(min(matches.count, 4)):
                element = matches[index]
                text = (element.info.get("text") or "").strip()
                if re.fullmatch(rf"{re.escape(label)}(?:\s*\(\d+\))?", text):
                    element.click()
                    human_pause(android_cfg, scale=1.1)
                    return
        if d(text="Далее").exists(timeout=0.5) and d(text="Далее").info.get("clickable"):
            d(text="Далее").click()
            human_pause(android_cfg, scale=1.1)
            return
        if d(text="Next").exists(timeout=0.3) and d(text="Next").info.get("clickable"):
            d(text="Next").click()
            human_pause(android_cfg, scale=1.1)
            return
        for rid in (f"{package}:id/wrj", f"{package}:id/p_e", f"{package}:id/p_o"):
            if d(resourceId=rid).exists(timeout=0.2):
                el = d(resourceId=rid)
                info = el.info
                if info.get("clickable") or info.get("text") in ("Далее", "Next"):
                    if info.get("clickable"):
                        el.click()
                    else:
                        click_center(d, info["bounds"])
                    human_pause(android_cfg, scale=1.1)
                    return
        # На экране редактора текст Далее часто не clickable — тапаем по bounds
        if d(text="Далее").exists(timeout=0.2):
            click_center(d, d(text="Далее").info["bounds"])
            human_pause(android_cfg, scale=1.1)
            return
        time.sleep(0.4)
    raise RuntimeError(f"Кнопка «Далее» не найдена ({stage})")


def _on_publish_screen(d) -> bool:
    return bool(
        d(text="Опубликовать").exists(timeout=0.4)
        or d(text="Post").exists(timeout=0.2)
        or d(text="Черновики").exists(timeout=0.2)
    )


def _ensure_publish_screen(d, package: str, android_cfg: dict[str, Any]) -> None:
    if _on_publish_screen(d):
        return
    # Иногда остаёмся на редакторе — ещё раз Далее
    if d(text="Далее").exists(timeout=1) or d(text="Next").exists(timeout=0.3):
        _tap_next(d, package, android_cfg, stage="editor-retry")
    for _ in range(20):
        if _on_publish_screen(d):
            return
        time.sleep(0.4)
    raise RuntimeError("Экран публикации TikTok не открылся")


def _hide_keyboard_safe(d) -> None:
    """Скрыть клавиатуру, не уходя с экрана публикации."""
    try:
        d.hide_keyboard()
    except Exception:
        pass
    time.sleep(0.4)
    if _on_publish_screen(d):
        return
    # Если hide_keyboard недоступен — тап по пустой зоне (не Back!)
    w, h = d.window_size()
    d.click(int(w * 0.5), int(h * 0.12))
    time.sleep(0.5)


def _fill_caption(d, package: str, caption: str, android_cfg: dict[str, Any]) -> None:
    _ensure_publish_screen(d, package, android_cfg)

    field_rid = f"{package}:id/gv0"
    if d(resourceId=field_rid).exists(timeout=3):
        d(resourceId=field_rid).click()
    elif d(text="Добавьте описание...").exists(timeout=1):
        d(text="Добавьте описание...").click()
    elif d(textContains="описание").exists(timeout=1):
        d(textContains="описание").click()
    else:
        raise RuntimeError("Поле описания не найдено")

    human_pause(android_cfg, scale=0.5)
    try:
        d.clear_text()
    except Exception:
        pass
    text = (caption or "").strip()[:2100]
    d.send_keys(text, clear=True)
    human_pause(android_cfg, scale=0.6)
    _hide_keyboard_safe(d)
    _ensure_publish_screen(d, package, android_cfg)


def _location_already_set(d, package: str, location: str) -> bool:
    """В шапке блока локации после выбора текст меняется на название места."""
    header = f"{package}:id/y56"
    if d(resourceId=header).exists(timeout=1):
        txt = (d(resourceId=header).get_text() or "").strip()
        if txt == location:
            return True
    return False


def _tap_text_bounds(d, text: str) -> bool:
    xml = d.dump_hierarchy()
    # Предпочитаем нижние чипы локации (не текст в caption)
    matches: list[tuple[int, tuple[int, int, int, int]]] = []
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        if a.get("text") != text:
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        # чипы обычно ниже ~550px на 1600-экране; caption выше
        matches.append((y1, parsed))
    if not matches:
        return False
    matches.sort(key=lambda item: item[0], reverse=True)
    x1, y1, x2, y2 = matches[0][1]
    d.click((x1 + x2) // 2, (y1 + y2) // 2)
    return True


def _reveal_location_row(d, android_cfg: dict[str, Any]) -> None:
    """Прокрутить экран публикации, чтобы были видны чипы локации."""
    w, h = d.window_size()
    for _ in range(4):
        if d(text="Местоположение").exists(timeout=0.4) or d(text="Пхукет").exists(timeout=0.3):
            return
        if d(text="Location").exists(timeout=0.2) or d(text="Phuket").exists(timeout=0.2):
            return
        d.swipe(w // 2, int(h * 0.72), w // 2, int(h * 0.38), 0.35)
        human_pause(android_cfg, scale=0.4)


def _select_location(
    d,
    package: str,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
) -> str:
    """Выбрать чип локации на экране публикации (по умолчанию «Пхукет»)."""
    tk = publisher_cfg.get("tiktok") or {}
    location = (
        (android_cfg.get("tiktok") or {}).get("location")
        or tk.get("location")
        or "Пхукет"
    )
    aliases = list(tk.get("location_aliases") or [location, "Phuket"])
    if location not in aliases:
        aliases.insert(0, location)

    _ensure_publish_screen(d, package, android_cfg)
    _hide_keyboard_safe(d)
    _reveal_location_row(d, android_cfg)

    for name in aliases:
        if _location_already_set(d, package, name):
            return name

    for name in aliases:
        if d(text=name).exists(timeout=1.5) and _tap_text_bounds(d, name):
            human_pause(android_cfg, scale=0.7)
            if _location_already_set(d, package, name):
                return name
            header = f"{package}:id/y56"
            if d(resourceId=header).exists(timeout=0.8):
                hdr = (d(resourceId=header).get_text() or "").strip()
                if hdr and hdr not in ("Местоположение", "Location", "Add location"):
                    return hdr
            # Чип мог выбраться без смены header id — считаем успехом, если header стал name
            if d(text=name).exists(timeout=0.5) and not d(text="Местоположение").exists(timeout=0.3):
                return name
            if _location_already_set(d, package, name):
                return name
            # Если тапнули чип и header = name
            return name

    if d(text="Местоположение").exists(timeout=1):
        _tap_text_bounds(d, "Местоположение")
    elif d(text="Location").exists(timeout=0.5):
        _tap_text_bounds(d, "Location")
    else:
        raise RuntimeError(f"Локация «{location}» не найдена на экране публикации")

    human_pause(android_cfg, scale=0.6)
    for name in aliases:
        if d(text=name).exists(timeout=3):
            _tap_text_bounds(d, name)
            human_pause(android_cfg, scale=0.7)
            return name
        if d(textContains=name).exists(timeout=1):
            d(textContains=name).click()
            human_pause(android_cfg, scale=0.7)
            return name

    if d(className="android.widget.EditText").exists(timeout=2):
        d(className="android.widget.EditText").click()
        d.send_keys(location, clear=True)
        human_pause(android_cfg, scale=0.8)
        if d(text=location).exists(timeout=4):
            _tap_text_bounds(d, location)
            human_pause(android_cfg, scale=0.7)
            return location
        if d(textContains="Phuket").exists(timeout=2):
            d(textContains="Phuket").click()
            human_pause(android_cfg, scale=0.7)
            return "Phuket"

    raise RuntimeError(f"Не удалось выбрать локацию «{location}»")


def _publish_or_stop(
    d,
    package: str,
    *,
    confirm_post: bool,
    android_cfg: dict[str, Any],
    channel: str = "tiktok",
) -> ChannelResult:
    if not confirm_post:
        if d(text="Черновики").exists(timeout=2):
            d(text="Черновики").click()
            human_pause(android_cfg)
            return ChannelResult(
                channel=channel,
                ok=True,
                skipped=True,
                reason="stopped_before_publish",
                note="Caption+location готовы → Черновики (--live для поста)",
            )
        return ChannelResult(
            channel=channel,
            ok=True,
            skipped=True,
            reason="stopped_before_publish",
            note="Экран публикации без «Опубликовать». Запустите с --live",
        )

    if d(text="Опубликовать").exists(timeout=3):
        d(text="Опубликовать").click()
    elif d(resourceId=f"{package}:id/shd").exists(timeout=1):
        d(resourceId=f"{package}:id/shd").click()
    elif d(text="Post").exists(timeout=1):
        d(text="Post").click()
    else:
        return ChannelResult(
            channel=channel,
            ok=False,
            reason="publish_button_not_found",
        )
    human_pause(android_cfg, scale=1.5)
    return ChannelResult(
        channel=channel,
        ok=True,
        note="Нажато «Опубликовать» — дождитесь загрузки в TikTok",
    )


def _album_name(
    publisher_cfg: dict[str, Any],
    android_cfg: dict[str, Any],
    *,
    kind: str,
) -> str:
    tk = {**(publisher_cfg.get("tiktok") or {}), **(android_cfg.get("tiktok") or {})}
    if kind == "video":
        return tk.get("video_album") or tk.get("album") or "Видео"
    return tk.get("carousel_album") or tk.get("album") or ALBUM_NAME


def _tiktok_gallery_selection_count(d) -> int | None:
    """Сколько фото реально выбрано (по кнопке «Далее (N)» / бейджам)."""
    import re

    for label in ("Далее", "Next"):
        if not d(textStartsWith=label).exists(timeout=0.35):
            continue
        try:
            texts = []
            n = d(textStartsWith=label).count
            for i in range(min(n, 4)):
                texts.append(d(textStartsWith=label)[i].info.get("text") or "")
        except Exception:
            texts = [d(textStartsWith=label).info.get("text") or ""]
        for t in texts:
            m = re.search(r"\((\d+)\)", t)
            if m:
                return int(m.group(1))
    xml = d.dump_hierarchy() or ""
    m = re.search(r"(?:Далее|Next)\s*\((\d+)\)", xml)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:Выбрано|Selected)\D{0,8}(\d+)", xml, re.I)
    if m:
        return int(m.group(1))
    return None


def _enable_tiktok_multi_select(d, android_cfg: dict[str, Any]) -> bool:
    """Включить multi-select только по точным UI-меткам, без координат."""
    if (
        d(description="Выбрано").exists(timeout=0.4)
        or d(description="Selected").exists(timeout=0.2)
        or d(textContains="Выбрано").exists(timeout=0.2)
    ):
        return True
    for label in (
        "Выбрать несколько",
        "Select multiple",
        "Multiple",
        "Выбрать",
        "Select",
    ):
        if click_text_or_desc(d, label, timeout=0.7):
            human_pause(android_cfg, scale=0.4)
            return True
        if d(descriptionContains=label).exists(timeout=0.3):
            d(descriptionContains=label).click()
            human_pause(android_cfg, scale=0.4)
            return True
    return False


def _tiktok_photo_thumbs(d) -> list[tuple[int, int, int, int]]:
    """Ячейки сетки фото: (x1,y1,x2,y2), сверху-слева."""
    xml = d.dump_hierarchy()
    candidates: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        if a.get("clickable") != "true":
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        bw, bh = x2 - x1, y2 - y1
        if y1 < 220 or y2 > 1380 or bw < 100 or bh < 100:
            continue
        if bw > 500:
            continue
        rid = a.get("resource-id", "")
        if rid.endswith("/suh"):
            continue
        desc = (a.get("content-desc") or "").lower()
        if "видео" in desc or "video" in desc:
            continue
        key = (x1 // 30, y1 // 30)
        if key in seen:
            continue
        seen.add(key)
        candidates.append((x1, y1, x2, y2))
    return carousel_bounds_pick_order(candidates)


def _select_photos(
    d,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
) -> int:
    """Вкладка Фото → multi-select → несколько изображений (не одно!)."""
    tk = publisher_cfg.get("tiktok") or {}
    max_n = int((tk.get("carousel") or {}).get("max_images") or 8)
    max_n = max(2, min(max_n, 35))

    if d(description="Фото").exists(timeout=2):
        d(description="Фото").click()
        time.sleep(0.8)
    elif d(text="Фото").exists(timeout=1):
        _tap_text_bounds(d, "Фото")
        time.sleep(0.8)

    if not _enable_tiktok_multi_select(d, android_cfg):
        raise RuntimeError("TikTok carousel: режим выбора нескольких фото не найден")

    thumbs = _tiktok_photo_thumbs(d)
    if len(thumbs) < 2:
        raise RuntimeError(
            f"TikTok carousel: в галерее мало фото-ячеек ({len(thumbs)})"
        )

    # Кружок выбора (правый верх ячейки). Порядок: slide_01 (ценовой хук) первым.
    for x1, y1, x2, y2 in thumbs[:max_n]:
        cx = int(x1 + (x2 - x1) * 0.82)
        cy = int(y1 + (y2 - y1) * 0.18)
        d.click(cx, cy)
        time.sleep(0.4)
        if len(_tiktok_photo_thumbs(d)) < 2:
            d.press("back")
            time.sleep(0.5)
            _enable_tiktok_multi_select(d, android_cfg)

    confirmed = _tiktok_gallery_selection_count(d)
    if confirmed is not None and confirmed < 2:
        raise RuntimeError(
            f"TikTok carousel: в UI выбрано только {confirmed} фото "
            "(нужен режим «несколько»). Перезапустите канал."
        )
    if confirmed is None:
        confirmed = min(len(thumbs), max_n)
        if confirmed < 2:
            raise RuntimeError("TikTok carousel: нужно ≥2 фото")
    human_pause(android_cfg, scale=0.6)
    return confirmed


class TikTokChannel:
    name = "tiktok"

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
                channel=self.name,
                ok=False,
                skipped=True,
                reason="no video",
            )

        package = android_cfg.get("packages", {}).get("tiktok", PKG_DEFAULT)
        caption = job.caption_social or ""
        note = (
            f"TikTok: video={job.device_video or job.local_video}; "
            f"caption_len={len(caption)}; package={package}"
        )

        if dry_run:
            return ChannelResult(
                channel=self.name,
                ok=True,
                skipped=True,
                reason="dry-run",
                note=note,
            )

        ui = android_cfg.get("ui_automation") or {}
        if not ui.get("enabled"):
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
            album = _album_name(publisher_cfg, android_cfg, kind="video")
            _open_tiktok(d, package, android_cfg)
            _tap_create(d, android_cfg)
            _tap_upload(d, package, android_cfg)
            _select_album(d, android_cfg, album)
            _select_latest_video(d, android_cfg)
            _tap_next(d, package, android_cfg, stage="gallery")
            _tap_next(d, package, android_cfg, stage="editor")
            _fill_caption(d, package, caption, android_cfg)
            chosen_loc = _select_location(d, package, android_cfg, publisher_cfg)
            note = f"{note}; location={chosen_loc}"
            result = _publish_or_stop(
                d,
                package,
                confirm_post=confirm_post,
                android_cfg=android_cfg,
                channel=self.name,
            )
            result = attach_post_url(d, result, self.name, android_cfg, confirm_post=confirm_post)
            if result.note:
                result.note = f"{result.note}; {note}"
            else:
                result.note = note
            return result
        except Exception as e:
            return ChannelResult(
                channel=self.name,
                ok=False,
                reason=str(e),
                note=note,
            )


class TikTokCarouselChannel:
    """TikTok photo post / карусель изображений (не видео)."""

    name = "tiktok_carousel"

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

        package = android_cfg.get("packages", {}).get("tiktok", PKG_DEFAULT)
        caption = job.caption_social or ""
        note = (
            f"TikTok carousel: images_available={len(images)}; "
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
            album = device_media_album(
                job.device_images,
                _album_name(publisher_cfg, android_cfg, kind="carousel"),
            )
            _open_tiktok(d, package, android_cfg)
            _tap_create(d, android_cfg)
            _tap_upload(d, package, android_cfg)
            _select_album(d, android_cfg, album)
            n = _select_photos(d, android_cfg, publisher_cfg)
            _tap_next(d, package, android_cfg, stage="gallery")
            # photo flow: иногда один экран редактора, иногда сразу publish
            if not _on_publish_screen(d):
                if d(text="Далее").exists(timeout=2) or d(text="Next").exists(timeout=0.5):
                    _tap_next(d, package, android_cfg, stage="editor")
            _fill_caption(d, package, caption, android_cfg)
            chosen_loc = _select_location(d, package, android_cfg, publisher_cfg)
            note = f"{note}; selected={n}; location={chosen_loc}"
            result = _publish_or_stop(
                d,
                package,
                confirm_post=confirm_post,
                android_cfg=android_cfg,
                channel=self.name,
            )
            result = attach_post_url(d, result, self.name, android_cfg, confirm_post=confirm_post)
            result.note = f"{(result.note or '')}; {note}".strip("; ")
            return result
        except Exception as e:
            return ChannelResult(
                channel=self.name,
                ok=False,
                reason=str(e),
                note=note,
            )
