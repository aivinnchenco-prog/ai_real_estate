from __future__ import annotations

import re
import time
from typing import Any
from xml.etree import ElementTree as ET

from ..android.ui import connect_device, dismiss_permissions, ensure_unlocked
from ..models import PublishJob
from .base import (
    ChannelResult,
    bounds_center,
    carousel_grid_pick_order,
    count_selected_gallery_photos,
    device_media_album,
    gallery_selection_state,
    require_designed_carousel,
    select_carousel_photos_toggle_safe,
)
from ._post_url import attach_post_url

PKG_DEFAULT = "com.linkedin.android"
ALBUM_DEFAULT = "publisher_social"


def _package(android_cfg: dict[str, Any]) -> str:
    return android_cfg.get("packages", {}).get("linkedin", PKG_DEFAULT)


def _li_cfg(publisher_cfg: dict[str, Any], android_cfg: dict[str, Any]) -> dict[str, Any]:
    base = dict(publisher_cfg.get("linkedin") or {})
    base.update(android_cfg.get("linkedin") or {})
    return base


def _parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _pause(ms: float = 250) -> None:
    """Короткая пауза только для LinkedIn (без глобального human_delay)."""
    time.sleep(max(0.05, ms / 1000.0))


def _wait_any(d, checks: list, *, attempts: int = 12, step: float = 0.25) -> bool:
    for _ in range(attempts):
        for check in checks:
            try:
                if check():
                    return True
            except Exception:
                continue
        time.sleep(step)
    return False


def _open_linkedin(d, package: str, android_cfg: dict[str, Any]) -> None:
    ensure_unlocked(d)
    d.press("home")
    _pause(200)
    d.app_stop(package)
    _pause(300)
    d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    _pause(700)
    ensure_unlocked(d)
    dismiss_permissions(d)
    for label in ("Пропустить", "Skip", "Не сейчас", "Not now"):
        if d(text=label).exists(timeout=0.35) or d(description=label).exists(timeout=0.2):
            (d(text=label) if d(text=label).exists() else d(description=label)).click()
            _pause(350)
    # ждём именно compose-кнопку (Главная появляется раньше и раньше давала ложный ready)
    if not _wait_any(
        d,
        [
            lambda: d(description="Создать публикацию").exists(timeout=0.25),
            lambda: d(textContains="Начать публикацию").exists(timeout=0.2),
            lambda: d(text="Создать публикацию").exists(timeout=0.2),
        ],
        attempts=24,
        step=0.35,
    ):
        # иногда feed под Главной — тапнуть Главная и ещё подождать
        if d(descriptionContains="Главная").exists(timeout=0.4):
            d(descriptionContains="Главная").click()
            _pause(500)
        if not _wait_any(
            d,
            [
                lambda: d(description="Создать публикацию").exists(timeout=0.25),
                lambda: d(textContains="Начать публикацию").exists(timeout=0.2),
            ],
            attempts=12,
            step=0.35,
        ):
            raise RuntimeError("LinkedIn: лента не загрузилась (нет «Создать публикацию»)")


def _open_compose(d, android_cfg: dict[str, Any]) -> None:
    if d(description="Создать публикацию").exists(timeout=4):
        d(description="Создать публикацию").click()
    elif d(textContains="Начать публикацию").exists(timeout=0.8):
        d(textContains="Начать публикацию").click()
    elif d(text="Создать публикацию").exists(timeout=0.5):
        d(text="Создать публикацию").click()
    else:
        raise RuntimeError("LinkedIn: кнопка «Создать публикацию» не найдена")
    if not _wait_any(
        d,
        [
            lambda: d(textContains="Поделитесь своими мыслями").exists(timeout=0.2),
            lambda: d(description="Фото").exists(timeout=0.2),
            lambda: d(text="Разместить").exists(timeout=0.2),
            lambda: d(className="android.widget.EditText").exists(timeout=0.2),
        ],
        attempts=14,
        step=0.25,
    ):
        raise RuntimeError("LinkedIn: экран создания публикации не открылся")


def _tap_photo_button(d, package: str) -> None:
    # resource-id варианты + a11y
    for rid in (
        f"{package}:id/add_image_button",
        f"{package}:id/media_button",
        f"{package}:id/photo_button",
        f"{package}:id/share_compose_photo_button",
    ):
        if d(resourceId=rid).exists(timeout=0.35):
            d(resourceId=rid).click()
            return
    for label in ("Фото", "Photo", "Изображение", "Image", "Медиа", "Media"):
        if d(description=label).exists(timeout=0.5):
            d(description=label).click()
            return
        if d(descriptionContains=label).exists(timeout=0.3):
            d(descriptionContains=label).click()
            return
        if d(text=label).exists(timeout=0.3):
            d(text=label).click()
            return
    # нижняя панель compose: несколько точек
    w, h = d.window_size()
    for x_ratio in (0.10, 0.16, 0.22, 0.28):
        d.click(int(w * x_ratio), int(h * 0.90))
        _pause(350)
        if (
            d(description="Подборки").exists(timeout=0.4)
            or d(text="На этом устройстве").exists(timeout=0.3)
            or d(text="Закрыть").exists(timeout=0.3)
            or d(textContains="Галерея").exists(timeout=0.3)
        ):
            return
        if d(description="Фото").exists(timeout=0.3):
            d(description="Фото").click()
            return
    raise RuntimeError("LinkedIn: кнопка «Фото» не найдена")


def _collect_unselected_linkedin_photos(xml: str) -> list[tuple[int, int, int, int]]:
    photos: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()

    def _collect(pred) -> None:
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            if not pred(a):
                continue
            desc = a.get("content-desc") or ""
            if gallery_selection_state(desc) == "selected":
                continue
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            if y1 < 220 or (y2 - y1) < 80 or (x2 - x1) < 80:
                continue
            y1, x1, cx, cy = bounds_center(parsed)
            key = (cx, cy)
            if key in seen:
                continue
            seen.add(key)
            photos.append((y1, x1, cx, cy))

    _collect(lambda a: "дата и время" in (a.get("content-desc") or "").lower())
    if not photos:
        _collect(lambda a: "photo taken on" in (a.get("content-desc") or "").lower())
    if not photos:

        def _grid_cell(a: dict[str, str]) -> bool:
            if a.get("clickable") != "true":
                return False
            desc = (a.get("content-desc") or "").lower()
            if any(x in desc for x in ("назад", "back", "готово", "done", "подборк")):
                return False
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed:
                return False
            x1, y1, x2, y2 = parsed
            w_cell, h_cell = x2 - x1, y2 - y1
            return y1 >= 280 and w_cell >= 120 and h_cell >= 120 and abs(w_cell - h_cell) <= 90

        _collect(_grid_cell)
    return carousel_grid_pick_order(photos)


def _add_photos(
    d,
    android_cfg: dict[str, Any],
    *,
    package: str,
    album: str,
    max_images: int,
) -> int:
    max_images = max(1, min(int(max_images), 9))
    _tap_photo_button(d, package)
    _pause(400)
    dismiss_permissions(d)

    if d(text="Закрыть").exists(timeout=0.8):
        d(text="Закрыть").click()
        _pause(300)

    if d(description="Подборки").exists(timeout=1.2):
        d(description="Подборки").click()
        _pause(450)
    if d(text="На этом устройстве").exists(timeout=1.2):
        d(text="На этом устройстве").click()
        _pause(500)
    if d(text=album).exists(timeout=2):
        d(text=album).click()
    elif d(textContains=album).exists(timeout=0.6):
        d(textContains=album).click()
    else:
        raise RuntimeError(f"LinkedIn: альбом «{album}» не найден")
    _pause(400)

    selected = select_carousel_photos_toggle_safe(
        d,
        max_images=max_images,
        collect_unselected=_collect_unselected_linkedin_photos,
        pause_s=0.15,
        min_selected=1,
    )
    if d(text="Готово").exists(timeout=2):
        d(text="Готово").click()
    elif d(description="Готово").exists(timeout=0.5):
        d(description="Готово").click()
    elif d(text="Done").exists(timeout=0.4):
        d(text="Done").click()
    else:
        raise RuntimeError("LinkedIn: нет «Готово» после выбора фото")
    _pause(500)

    if d(text="Далее").exists(timeout=2.5) or d(description="Далее").exists(timeout=0.4):
        (d(text="Далее") if d(text="Далее").exists() else d(description="Далее")).click()
        _pause(450)
    elif d(text="Next").exists(timeout=0.4):
        d(text="Next").click()
        _pause(450)
    return selected


def _set_caption(d, caption: str, android_cfg: dict[str, Any]) -> None:
    if not caption:
        return
    if d(textContains="Поделитесь своими мыслями").exists(timeout=1.2):
        d(textContains="Поделитесь своими мыслями").click()
    elif d(className="android.widget.EditText").exists(timeout=0.6):
        d(className="android.widget.EditText").click()
    _pause(200)
    if d(className="android.widget.EditText").exists(timeout=1.5):
        d(className="android.widget.EditText").set_text(caption[:3000])
    else:
        try:
            d.send_keys(caption[:3000])
        except Exception as e:
            raise RuntimeError(f"LinkedIn: не удалось ввести текст: {e}") from e
    _pause(200)
    try:
        d.hide_keyboard()
    except Exception:
        pass
    _pause(200)


def _publish_or_stop(
    d,
    *,
    confirm_post: bool,
    android_cfg: dict[str, Any],
) -> ChannelResult:
    if not d(text="Разместить").exists(timeout=2.5) and not d(text="Post").exists(timeout=0.4):
        return ChannelResult(
            channel="linkedin",
            ok=False,
            reason="post_button_not_found",
        )
    if not confirm_post:
        return ChannelResult(
            channel="linkedin",
            ok=True,
            skipped=True,
            reason="stopped_before_publish",
            note="Пост готов (фото+текст, Общедоступно), «Разместить» не нажат (--live)",
        )
    if d(text="Разместить").exists(timeout=0.5):
        d(text="Разместить").click()
    else:
        d(text="Post").click()
    _pause(600)
    return ChannelResult(
        channel="linkedin",
        ok=True,
        note="Нажато «Разместить» — дождитесь публикации в LinkedIn",
    )


class LinkedInChannel:
    name = "linkedin"

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
        cfg = _li_cfg(publisher_cfg, android_cfg)
        caption = (job.caption_social or job.caption_fb or "").strip()
        if cfg.get("use_caption_fb"):
            caption = (job.caption_fb or job.caption_social or "").strip()

        if not images and not caption:
            return ChannelResult(
                channel=self.name,
                ok=False,
                skipped=True,
                reason="no images/caption",
            )

        package = _package(android_cfg)
        album = device_media_album(
            job.device_images,
            cfg.get("album") or ALBUM_DEFAULT,
        )
        max_images = min(len(images), int(cfg.get("max_images") or 9))
        note = (
            f"LinkedIn: images_available={len(images)}; source={job.images_source}; "
            f"caption_len={len(caption)}; album={album}"
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
            _open_linkedin(d, package, android_cfg)
            _open_compose(d, android_cfg)
            n = 0
            if images:
                n = _add_photos(
                    d,
                    android_cfg,
                    package=package,
                    album=album,
                    max_images=max_images,
                )
            _set_caption(d, caption, android_cfg)
            note = f"{note}; selected={n}; audience=Общедоступно"
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
