from __future__ import annotations

import re
import time
from typing import Any
from xml.etree import ElementTree as ET

from ..android.ui import connect_device, dismiss_permissions, ensure_unlocked, human_pause
from ..models import PublishJob
from .base import (
    ChannelResult,
    carousel_grid_pick_order,
    device_media_album,
    gallery_selection_state,
    require_designed_carousel,
    select_carousel_photos_toggle_safe,
)
from ._post_url import attach_post_url

PKG_DEFAULT = "com.twitter.android"
ALBUM_DEFAULT = "Видео"


def _package(android_cfg: dict[str, Any]) -> str:
    return android_cfg.get("packages", {}).get("twitter", PKG_DEFAULT)


def _tw_cfg(publisher_cfg: dict[str, Any], android_cfg: dict[str, Any]) -> dict[str, Any]:
    base = dict(publisher_cfg.get("twitter") or {})
    base.update(android_cfg.get("twitter") or {})
    return base


def _parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _open_twitter(d, package: str, android_cfg: dict[str, Any]) -> None:
    # сброс зависшего системного photo picker / галереи поверх X
    for extra in (
        "com.google.android.photopicker",
        "com.google.android.providers.media.module",
        "com.android.providers.media.module",
    ):
        try:
            d.app_stop(extra)
        except Exception:
            pass
    d.press("home")
    time.sleep(0.35)
    d.app_stop(package)
    time.sleep(0.45)
    d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    human_pause(android_cfg, scale=1.3)
    ensure_unlocked(d)
    dismiss_permissions(d)
    for label in ("Пропустить", "Skip", "Not now", "Не сейчас", "Accept", "Принять"):
        if d(text=label).exists(timeout=0.5) or d(description=label).exists(timeout=0.3):
            (d(text=label) if d(text=label).exists() else d(description=label)).click()
            time.sleep(0.7)
    for _ in range(20):
        cur = (d.app_current() or {}).get("package") or ""
        if cur != package:
            d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
            time.sleep(0.8)
            ensure_unlocked(d)
        if d(description="Опубликовать пост").exists(timeout=0.45) or d(
            description="Главная"
        ).exists(timeout=0.3):
            return
        # иногда остаёмся на профиле/sheet — на Главную
        if d(description="Главная").exists(timeout=0.2):
            d(description="Главная").click()
            time.sleep(0.5)
            return
        time.sleep(0.45)
    raise RuntimeError("Twitter/X: главный экран не открылся")


def _open_compose(d, android_cfg: dict[str, Any]) -> None:
    # FAB на главной — content-desc «Опубликовать пост»
    if d(description="Опубликовать пост").exists(timeout=3):
        # может быть несколько (в ленте и FAB) — клик по последнему/FAB
        count = d(description="Опубликовать пост").count
        d(description="Опубликовать пост")[count - 1].click()
    else:
        raise RuntimeError("Twitter/X: кнопка «Опубликовать пост» не найдена")
    human_pause(android_cfg)
    for _ in range(15):
        if (
            d(textContains="Что происходит").exists(timeout=0.4)
            or d(description="Фотографии").exists(timeout=0.3)
            or d(textContains="Добавьте комментарий").exists(timeout=0.3)
        ):
            return
        time.sleep(0.4)
    raise RuntimeError("Twitter/X: экран создания поста не открылся")


def _collect_gallery_media(
    d, *, videos_only: bool = False, photos_only: bool = False
) -> list[tuple[int, int, int, int]]:
    """Список (y, x, cx, cy) ячеек галереи. Видео отличаются «Длительность» в desc."""
    xml = d.dump_hierarchy()
    cells: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        desc = (a.get("content-desc") or "").lower()
        if "дата и время" not in desc and "photo taken on" not in desc:
            continue
        is_video = "длительность" in desc or "duration" in desc
        if videos_only and not is_video:
            continue
        if photos_only and is_video:
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        if y1 < 250 or (y2 - y1) < 80:
            continue
        key = ((x1 + x2) // 2, (y1 + y2) // 2)
        if key in seen:
            continue
        seen.add(key)
        cells.append((y1, x1, key[0], key[1]))
    return carousel_grid_pick_order(cells)


def _collect_unselected_twitter_photos(xml: str) -> list[tuple[int, int, int, int]]:
    cells: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        desc = a.get("content-desc") or ""
        low = desc.lower()
        if "дата и время" not in low and "photo taken on" not in low:
            continue
        if "длительность" in low or "duration" in low:
            continue
        if gallery_selection_state(desc) == "selected":
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        if y1 < 250 or (y2 - y1) < 80:
            continue
        key = ((x1 + x2) // 2, (y1 + y2) // 2)
        if key in seen:
            continue
        seen.add(key)
        cells.append((y1, x1, key[0], key[1]))
    return carousel_grid_pick_order(cells)


def _open_gallery_album(d, android_cfg: dict[str, Any], *, album: str) -> None:
    if d(description="Фотографии").exists(timeout=3):
        d(description="Фотографии").click()
    elif d(descriptionContains="Фото").exists(timeout=1):
        d(descriptionContains="Фото").click()
    else:
        raise RuntimeError("Twitter/X: кнопка «Фотографии» не найдена")
    human_pause(android_cfg, scale=0.9)
    dismiss_permissions(d)
    if d(text="Закрыть").exists(timeout=1):
        d(text="Закрыть").click()
        time.sleep(0.5)

    if d(description="Подборки").exists(timeout=2):
        d(description="Подборки").click()
        time.sleep(1.0)
    if d(text="На этом устройстве").exists(timeout=2):
        d(text="На этом устройстве").click()
        time.sleep(1.2)
    if d(text=album).exists(timeout=3):
        d(text=album).click()
    elif d(textContains=album).exists(timeout=1):
        d(textContains=album).click()
    else:
        raise RuntimeError(f"Twitter/X: альбом «{album}» не найден")
    human_pause(android_cfg, scale=0.7)


def _tap_gallery_done(d, android_cfg: dict[str, Any], *, kind: str) -> None:
    if d(text="Готово").exists(timeout=3):
        d(text="Готово").click()
    elif d(description="Готово").exists(timeout=1):
        d(description="Готово").click()
    else:
        raise RuntimeError(f"Twitter/X: нет «Готово» после выбора {kind}")
    human_pause(android_cfg, scale=1.0)


def _add_video(
    d,
    android_cfg: dict[str, Any],
    *,
    album: str,
) -> int:
    """Последнее видео из списка «Видео» (первая ячейка с длительностью)."""
    _open_gallery_album(d, android_cfg, album=album)
    w, h = d.window_size()
    for _ in range(6):
        videos = _collect_gallery_media(d, videos_only=True)
        if videos:
            # самое новое сверху слева
            _, __, cx, cy = videos[0]
            d.click(cx, cy)
            time.sleep(0.6)
            _tap_gallery_done(d, android_cfg, kind="видео")
            return 1
        d.swipe(w // 2, int(h * 0.78), w // 2, int(h * 0.38), 0.25)
        time.sleep(0.55)
    raise RuntimeError(f"Twitter/X: в «{album}» нет видео")


def _add_photos(
    d,
    android_cfg: dict[str, Any],
    *,
    album: str,
    max_images: int,
) -> int:
    # X позволяет максимум 4 изображения в одном посте
    max_images = max(1, min(int(max_images), 4))
    _open_gallery_album(d, android_cfg, album=album)

    selected = select_carousel_photos_toggle_safe(
        d,
        max_images=max_images,
        collect_unselected=_collect_unselected_twitter_photos,
        pause_s=0.3,
        min_selected=1,
    )
    _tap_gallery_done(d, android_cfg, kind="фото")
    return selected


def _set_caption(d, caption: str, android_cfg: dict[str, Any]) -> None:
    if not caption:
        return
    opened = False
    for label in ("Добавьте комментарий", "Что происходит", "What's happening"):
        if d(textContains=label[:12]).exists(timeout=1):
            d(textContains=label[:12]).click()
            opened = True
            break
    if not opened and d(className="android.widget.EditText").exists(timeout=1):
        d(className="android.widget.EditText").click()
        opened = True
    if not opened:
        raise RuntimeError("Twitter/X: поле текста не найдено")
    time.sleep(0.35)
    if d(className="android.widget.EditText").exists(timeout=2):
        d(className="android.widget.EditText").set_text(caption)
    else:
        d.send_keys(caption)
    time.sleep(0.35)
    try:
        d.hide_keyboard()
    except Exception:
        pass
    human_pause(android_cfg, scale=0.4)


def _set_location(d, android_cfg: dict[str, Any], cfg: dict[str, Any]) -> str:
    if not cfg.get("set_location", True):
        return ""
    location = (cfg.get("location") or "Phuket").strip()
    prefer = list(
        cfg.get("location_aliases")
        or [
            "Phuket, Thailand",
            "Amphoe Thalang, Changwat Phuket",
            "Amphoe Thalang",
            "Changwat Phuket, Thailand",
            location,
            "Пхукет",
        ]
    )
    w, h = d.window_size()
    for _ in range(4):
        for label in (
            "Добавить местоположение",
            "Add location",
            "Местоположение",
            "Location",
        ):
            if d(text=label).exists(timeout=0.8):
                d(text=label).click()
                break
            if d(description=label).exists(timeout=0.4):
                d(description=label).click()
                break
            if d(descriptionContains=label).exists(timeout=0.3):
                d(descriptionContains=label).click()
                break
        else:
            d.swipe(w // 2, int(h * 0.65), w // 2, int(h * 0.35), 0.25)
            time.sleep(0.4)
            continue
        human_pause(android_cfg, scale=0.8)
        break
    else:
        return ""

    dismiss_permissions(d)
    for label in ("Разрешить", "При использовании приложения", "Allow", "While using the app"):
        if d(text=label).exists(timeout=0.8):
            d(text=label).click()
            time.sleep(0.8)
    query = "Amphoe Thalang" if "thalang" in " ".join(prefer).lower() else location
    if d(className="android.widget.EditText").exists(timeout=2):
        try:
            d(className="android.widget.EditText").set_text(query)
            time.sleep(1.8)
        except Exception:
            pass

    chosen = ""
    xml = d.dump_hierarchy()
    for target in prefer:
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            t = a.get("text") or ""
            if t != target and target not in t:
                continue
            if "EditText" in (a.get("class") or ""):
                continue
            low = t.lower()
            if "hong kong" in low or "гонконг" in low:
                continue
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed or parsed[1] < 200:
                continue
            d.click((parsed[0] + parsed[2]) // 2, (parsed[1] + parsed[3]) // 2)
            chosen = t
            human_pause(android_cfg, scale=0.7)
            break
        if chosen:
            break

    if not chosen:
        for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
            a = node.attrib
            t = a.get("text") or ""
            low = t.lower()
            if "EditText" in (a.get("class") or ""):
                continue
            if "hong kong" in low or "гонконг" in low:
                continue
            if "phuket" not in low and "пхукет" not in low and "thalang" not in low:
                continue
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed or parsed[1] < 200:
                continue
            d.click((parsed[0] + parsed[2]) // 2, (parsed[1] + parsed[3]) // 2)
            chosen = t
            human_pause(android_cfg, scale=0.7)
            break

    if d(text="Готово").exists(timeout=2):
        d(text="Готово").click()
        human_pause(android_cfg, scale=0.7)
    elif d(text="Done").exists(timeout=1):
        d(text="Done").click()
        human_pause(android_cfg, scale=0.7)
    elif not chosen:
        d.press("back")
        time.sleep(0.5)
        return ""
    return chosen or location

def _publish_or_stop(
    d,
    *,
    confirm_post: bool,
    android_cfg: dict[str, Any],
) -> ChannelResult:
    # если остались на пикере локации — закрыть
    if d(text="Отметить местоположение").exists(timeout=0.8) or d(text="Готово").exists(
        timeout=0.4
    ):
        if d(text="Готово").exists(timeout=0.5):
            d(text="Готово").click()
            human_pause(android_cfg, scale=0.6)
        else:
            d.press("back")
            time.sleep(0.6)
    if not (
        d(description="Опубликовать пост").exists(timeout=3)
        or d(text="Опубликовать пост").exists(timeout=0.5)
    ):
        return ChannelResult(
            channel="twitter",
            ok=False,
            reason="post_button_not_found",
        )
    if not confirm_post:
        return ChannelResult(
            channel="twitter",
            ok=True,
            skipped=True,
            reason="stopped_before_publish",
            note="Пост готов (медиа+текст), «Опубликовать пост» не нажат (--live)",
        )
    if d(description="Опубликовать пост").exists(timeout=1):
        # на экране compose кнопка в шапке
        d(description="Опубликовать пост").click()
    else:
        d(text="Опубликовать пост").click()
    human_pause(android_cfg, scale=1.5)
    return ChannelResult(
        channel="twitter",
        ok=True,
        note="Нажато «Опубликовать пост» — дождитесь отправки в X",
    )


class TwitterChannel:
    name = "twitter"

    def publish(
        self,
        job: PublishJob,
        *,
        dry_run: bool,
        android_cfg: dict[str, Any],
        publisher_cfg: dict[str, Any],
        confirm_post: bool = False,
    ) -> ChannelResult:
        images = job.device_images or job.local_images or job.image_urls
        has_video = bool(job.device_video or job.local_video or job.video_url)
        cfg = _tw_cfg(publisher_cfg, android_cfg)
        caption = (job.caption_x or job.caption_social or job.caption_fb or "").strip()
        if cfg.get("use_caption_fb"):
            caption = (job.caption_fb or job.caption_x or job.caption_social or "").strip()
        max_chars = int(cfg.get("max_chars") or 280)
        if len(caption) > max_chars:
            caption = caption[: max_chars - 1].rstrip() + "…"

        # video | images | auto (video если есть ролик)
        media_mode = str(cfg.get("media") or "video").strip().lower()
        if media_mode == "auto":
            media_mode = "video" if has_video else "images"
        if media_mode not in ("video", "images"):
            media_mode = "video" if has_video else "images"

        if media_mode == "video" and not has_video:
            return ChannelResult(
                channel=self.name,
                ok=False,
                skipped=True,
                reason="no video",
            )
        if media_mode == "images":
            bad = require_designed_carousel(job, self.name)
            if bad:
                return bad
        if media_mode == "images" and not images and not caption:
            return ChannelResult(
                channel=self.name,
                ok=False,
                skipped=True,
                reason="no images/caption",
            )

        package = _package(android_cfg)
        if media_mode == "video":
            album = cfg.get("video_album") or cfg.get("album") or ALBUM_DEFAULT
        else:
            album = device_media_album(
                job.device_images,
                cfg.get("image_album") or cfg.get("album") or "publisher_social",
            )
        max_images = min(len(images) if images else 4, int(cfg.get("max_images") or 4))
        note = (
            f"Twitter/X: media={media_mode}; video={'yes' if has_video else 'no'}; "
            f"images_available={len(images)}; caption_len={len(caption)}; album={album}"
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
            ensure_unlocked(d)
            _open_twitter(d, package, android_cfg)
            _open_compose(d, android_cfg)
            _set_caption(d, caption, android_cfg)
            if media_mode == "video":
                n = _add_video(d, android_cfg, album=album)
                note = f"{note}; selected_video={n}"
            else:
                n = _add_photos(d, android_cfg, album=album, max_images=max_images)
                note = f"{note}; selected={n}"
            loc = _set_location(d, android_cfg, cfg)
            note = f"{note}; location={loc or '-'}"
            result = _publish_or_stop(
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
