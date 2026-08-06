from __future__ import annotations

import re
import time
import urllib.request
from typing import Any
from xml.etree import ElementTree as ET

from ..android.ui import connect_device, dismiss_permissions, human_pause
from ..models import PublishJob
from . import fb_marketplace as mp
from ._fb_publish import confirm_publish_or_stop
from .base import (
    ChannelResult,
    carousel_bounds_pick_order,
    count_selected_gallery_photos,
    device_media_album,
    require_designed_carousel,
)
from ._post_url import attach_post_url

PKG_DEFAULT = "com.facebook.katana"
ALBUM_DEFAULT = "publisher_social"


def _package(android_cfg: dict[str, Any]) -> str:
    return android_cfg.get("packages", {}).get("facebook", PKG_DEFAULT)


def _grp_cfg(publisher_cfg: dict[str, Any], android_cfg: dict[str, Any]) -> dict[str, Any]:
    base = dict(publisher_cfg.get("fb_groups") or {})
    base.update(android_cfg.get("fb_groups") or {})
    return base


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
    """share/g/... → numeric group id; обычные /groups/ID/ — как есть."""
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
    raise RuntimeError(f"Не удалось извлечь ID группы из URL: {url}")


def _open_group(d, package: str, group_id: str, android_cfg: dict[str, Any]) -> None:
    from ..android.ui import ensure_unlocked

    d.press("home")
    time.sleep(0.35)
    d.app_stop(package)
    time.sleep(0.45)
    d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    human_pause(android_cfg, scale=1.0)
    ensure_unlocked(d)
    d.shell(f'am start -a android.intent.action.VIEW -d "fb://group/{group_id}"')
    human_pause(android_cfg, scale=1.3)
    ensure_unlocked(d)
    dismiss_permissions(d)
    # ждём именно композер / property UI — не обрываем на «В группе» в шапке
    for i in range(24):
        xml = d.dump_hierarchy()
        if "Вступить в группу" in xml and "В группе" not in xml:
            raise RuntimeError(
                f"Вы ещё не участник группы {group_id}. Вступите вручную, затем повторите."
            )
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


def _discussion_compose_open(d) -> bool:
    """Уже на экране создания поста в группе."""
    return bool(
        d(text="Галерея").exists(timeout=0.35)
        or d(description="Галерея").exists(timeout=0.25)
        or d(textContains="общедоступную публикацию").exists(timeout=0.25)
        or d(descriptionContains="Название публикации").exists(timeout=0.25)
        or (
            d(text="Опубликовать").exists(timeout=0.25)
            and d(descriptionContains="Местоположение").exists(timeout=0.2)
        )
    )


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


def _collect_discussion_photo_bounds(xml: str) -> list[tuple[int, int, int, int]]:
    """Gallery thumbnails as (x1,y1,x2,y2), slide_01 (price hook) first."""
    bounds_list: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()

    def _add(parsed: tuple[int, int, int, int]) -> None:
        x1, y1, x2, y2 = parsed
        if y1 < 180 or (x2 - x1) < 80 or (y2 - y1) < 80:
            return
        key = (x1 // 80, y1 // 80)
        if key in seen:
            return
        seen.add(key)
        bounds_list.append(parsed)

    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        if a.get("clickable") != "true":
            continue
        desc = (a.get("content-desc") or "").lower()
        if any(x in desc for x in ("сделать", "camera", "назад", "back", "готово", "done")):
            continue
        if not any(
            token in desc
            for token in ("фото", "photo", "image", "изображен", "дата и время", "photo taken on")
        ):
            continue
        parsed = _parse_bounds(a.get("bounds", ""))
        if parsed:
            _add(parsed)

    if len(bounds_list) < 2:
        bounds_list.clear()
        seen.clear()
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            if a.get("clickable") != "true":
                continue
            desc = (a.get("content-desc") or "").lower()
            if any(
                x in desc
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
            if y1 < 220 or w_cell < 100 or h_cell < 100:
                continue
            if abs(w_cell - h_cell) > 100:
                continue
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
    return ""


def _read_discussion_selected_count(d) -> int | None:
    xml = d.dump_hierarchy() or ""
    for node in ET.fromstring(xml).iter("node"):
        for attr in ("text", "content-desc"):
            raw = node.attrib.get(attr) or ""
            low = raw.lower()
            if "выбрано" not in low and "selected" not in low:
                continue
            match = re.search(r"(\d+)", raw)
            if match:
                return int(match.group(1))
    counted = count_selected_gallery_photos(xml)
    return counted if counted > 0 else None


def _select_discussion_photos(
    d,
    android_cfg: dict[str, Any],
    *,
    max_images: int,
) -> int:
    """
    FB Groups discussion: enable multi-select, then tap each thumbnail badge once.

    Without multi-select, every tap replaces the previous pick (only 1 photo posts).
    """
    max_images = max(1, min(int(max_images), 10))
    time.sleep(0.6)
    bounds_list = _collect_discussion_photo_bounds(d.dump_hierarchy() or "")
    if not bounds_list:
        raise RuntimeError("FB Groups: в альбоме карусели не найдены фото")
    targets = bounds_list[:max_images]
    mode = _arm_discussion_multi_select(d, android_cfg, targets[0])
    if not mode:
        raise RuntimeError(
            "FB Groups: не удалось включить выбор нескольких фото в галерее."
        )
    skip_first = mode == "long_press"
    for index, bounds in enumerate(targets):
        if skip_first and index == 0:
            continue
        cx, cy = _fb_photo_checkbox_point(bounds)
        d.click(cx, cy)
        time.sleep(0.45)
    ui_count = _read_discussion_selected_count(d)
    if ui_count is None:
        ui_count = len(targets)
    if len(targets) >= 2 and ui_count < 2:
        raise RuntimeError(
            f"FB Groups: multi-select не сработал — выбрано {ui_count} из {len(targets)}"
        )
    if ui_count < len(targets):
        raise RuntimeError(
            f"FB Groups: выбрано {ui_count} фото, ожидалось {len(targets)}"
        )
    return ui_count


def _open_gallery_album(d, album: str, object_id: str = "") -> None:
    """Open the per-object carousel album before picking thumbnails."""
    candidates = [album]
    if object_id:
        from .base import carousel_folder_name

        named = carousel_folder_name(object_id)
        if named not in candidates:
            candidates.append(named)
        if object_id not in candidates:
            candidates.append(object_id)
    if not d(descriptionContains="Выбор альбома").exists(timeout=2):
        return
    d(descriptionContains="Выбор альбома").click()
    time.sleep(1.0)
    for name in candidates:
        if not name:
            continue
        if d(text=name).exists(timeout=0.8):
            d(text=name).click()
            time.sleep(1.0)
            return
        if d(descriptionContains=name).exists(timeout=0.5):
            d(descriptionContains=name).click()
            time.sleep(1.0)
            return
    raise RuntimeError(
        f"FB Groups: альбом «{album}» не найден в галерее "
        f"(ожидалась папка объекта на телефоне)"
    )


def _discussion_add_photos(
    d,
    android_cfg: dict[str, Any],
    *,
    album: str,
    max_images: int,
    object_id: str = "",
) -> int:
    max_images = max(1, min(int(max_images), 10))
    if d(text="Галерея").exists(timeout=3):
        d(text="Галерея").click()
    elif d(description="Галерея").exists(timeout=1):
        d(description="Галерея").click()
    elif d(descriptionContains="Фото/видео").exists(timeout=1):
        d(descriptionContains="Фото/видео").click()
    else:
        raise RuntimeError("Кнопка «Галерея» не найдена в композере группы")
    human_pause(android_cfg, scale=0.9)
    dismiss_permissions(d)

    _open_gallery_album(d, album, object_id=object_id)

    selected = _select_discussion_photos(d, android_cfg, max_images=max_images)
    for _ in range(15):
        if d(description="Далее").exists(timeout=0.5):
            d(description="Далее").click()
            break
        if d(text="Далее").exists(timeout=0.3):
            d(text="Далее").click()
            break
        time.sleep(0.3)
    else:
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
            _discussion_return_to_compose(d, android_cfg)
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
        _discussion_return_to_compose(d, android_cfg)
        return t
    raise RuntimeError(f"Не удалось выбрать место «{fallback}» для поста в группе")


def _discussion_return_to_compose(d, android_cfg: dict[str, Any]) -> None:
    for _ in range(6):
        if _discussion_compose_open(d):
            return
        d.press("back")
        human_pause(android_cfg, scale=0.4)
    if not _discussion_compose_open(d):
        raise RuntimeError("FB Groups: не удалось вернуться к экрану публикации")


def _discussion_publish_or_stop(
    d,
    *,
    confirm_post: bool,
    android_cfg: dict[str, Any],
    group_url: str,
) -> ChannelResult:
    return confirm_publish_or_stop(
        d,
        confirm_post=confirm_post,
        android_cfg=android_cfg,
        channel="fb_groups",
        before_publish=lambda: _discussion_return_to_compose(d, android_cfg),
        stopped_note=(
            f"Discussion-пост готов (фото+текст+место), Опубликовать не нажат (--live). "
            f"group={group_url}"
        ),
        success_note=f"Нажато «Опубликовать» в группу {group_url}",
    )


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
        bad = require_designed_carousel(job, self.name)
        if bad:
            return bad
        images = job.device_images or job.local_images or job.image_urls
        groups = job.fb_groups
        if not groups:
            return ChannelResult(
                channel=self.name,
                ok=False,
                skipped=True,
                reason="no groups in config/fb_groups_list.txt",
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
        max_groups = int(cfg.get("max_groups_per_object") or 2)
        targets = groups[:max_groups]
        album = device_media_album(
            job.device_images,
            cfg.get("album") or ALBUM_DEFAULT,
            object_id=job.object_id,
        )
        max_images = min(
            len(images),
            int(
                cfg.get("max_images")
                or (publisher_cfg.get("media") or {}).get("max_groups_images")
                or 8
            ),
        )
        package = _package(android_cfg)
        note = (
            f"FB Groups: targets={targets}; images_available={len(images)}; "
            f"caption_len={len(caption)}"
        )

        if not job.device_images and not dry_run:
            return ChannelResult(
                channel=self.name,
                ok=False,
                reason="device_images_missing",
                note=(
                    f"{note}; carousel not on phone — run with --live "
                    f"(auto push) or prepare --push-media"
                ),
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
        try:
            d = connect_device(android_cfg)
            d.screen_on()
            for url in targets:
                gid = resolve_group_id(url)
                _open_group(d, package, gid, android_cfg)
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
                    )
                elif mode == "discussion":
                    _open_discussion_compose(d, android_cfg)
                    n = 0
                    if images:
                        n = _discussion_add_photos(
                            d,
                            android_cfg,
                            album=album,
                            max_images=max_images,
                            object_id=job.object_id,
                        )
                    _discussion_set_caption(d, caption, android_cfg)
                    place = _discussion_set_place(d, android_cfg, publisher_cfg)
                    note = f"{note}; photos={n}; place={place}"
                    last = _discussion_publish_or_stop(
                        d,
                        confirm_post=confirm_post,
                        android_cfg=android_cfg,
                        group_url=url,
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
                    return last
            if last:
                last = attach_post_url(d, last, self.name, android_cfg, confirm_post=confirm_post)
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
