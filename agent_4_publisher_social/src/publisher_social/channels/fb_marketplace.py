from __future__ import annotations

import re
import time
from typing import Any
from xml.etree import ElementTree as ET

from ..android.ui import connect_device, dismiss_permissions, human_pause
from ..models import PublishJob
from .base import ChannelResult
PKG_DEFAULT = "com.facebook.katana"
ALBUM_DEFAULT = "brand_open_home"

# Notion / локальные названия → пункты Facebook (RU UI)
_TYPE_MAP = {
    "дом": "Дом",
    "house": "Дом",
    "villa": "Дом",
    "вилла": "Дом",
    "квартира": "Квартира",
    "apartment": "Квартира",
    "condo": "Квартира",
    "кондо": "Квартира",
    "апартаменты": "Квартира",
    "таунхаус": "Таунхаус",
    "townhouse": "Таунхаус",
    "town house": "Таунхаус",
}


def _parse_bounds(bounds: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _package(android_cfg: dict[str, Any]) -> str:
    return android_cfg.get("packages", {}).get("facebook", PKG_DEFAULT)


def _mp_cfg(publisher_cfg: dict[str, Any], android_cfg: dict[str, Any]) -> dict[str, Any]:
    base = dict(publisher_cfg.get("fb_marketplace") or {})
    base.update(android_cfg.get("fb_marketplace") or {})
    return base


def _tap_desc_contains(d, substr: str) -> bool:
    xml = d.dump_hierarchy()
    for node in ET.fromstring(xml).iter("node"):
        desc = node.attrib.get("content-desc") or ""
        if substr not in desc:
            continue
        parsed = _parse_bounds(node.attrib.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        d.click((x1 + x2) // 2, (y1 + y2) // 2)
        return True
    if d(text=substr).exists(timeout=0.4):
        d(text=substr).click()
        return True
    return False


def _map_property_type(raw: str | None) -> str:
    key = (raw or "").strip().lower()
    if key in _TYPE_MAP:
        return _TYPE_MAP[key]
    for k, v in _TYPE_MAP.items():
        if k in key:
            return v
    return "Дом"


def _format_rooms(value: float | int | None) -> str:
    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return str(value).rstrip("0").rstrip(".")


def _format_price(value: float | int | None) -> str:
    if value is None:
        return ""
    return str(int(round(float(value))))


def _ensure_personal_profile(d, android_cfg: dict[str, Any]) -> str:
    """Marketplace недоступен со страницы Page — нужен личный профиль."""
    d.shell('am start -a android.intent.action.VIEW -d "fb://marketplace"')
    human_pause(android_cfg, scale=1.1)
    xml = d.dump_hierarchy()
    if "Pages can't use Marketplace" in xml or "не могут использовать Marketplace" in xml:
        d.press("back")
        time.sleep(0.8)
        if d(description="Меню").exists(timeout=3):
            d(description="Меню").click()
            human_pause(android_cfg, scale=0.7)
        if _tap_desc_contains(d, "переключиться на ваш профиль") or _tap_desc_contains(
            d, "Открыть переключатель профиля"
        ):
            human_pause(android_cfg, scale=0.8)
            # личный профиль: не OpenHome / не страница
            for label in ("Ваш профиль", "Your profile"):
                if d(text=label).exists(timeout=1):
                    d(text=label).click()
                    human_pause(android_cfg)
                    break
            else:
                # закрыть sheet и тапнуть фото личного профиля в меню
                if d(description="Закрыть").exists(timeout=1):
                    d(description="Закрыть").click()
                    time.sleep(0.5)
                if d(descriptionContains="Фото профиля").exists(timeout=1):
                    # предпочесть не-OpenHome
                    xml2 = d.dump_hierarchy()
                    for node in ET.fromstring(xml2).iter("node"):
                        desc = node.attrib.get("content-desc") or ""
                        if "Фото профиля" in desc and "Open" not in desc:
                            parsed = _parse_bounds(node.attrib.get("bounds", ""))
                            if parsed:
                                x1, y1, x2, y2 = parsed
                                d.click((x1 + x2) // 2, (y1 + y2) // 2)
                                human_pause(android_cfg)
                                break
        d.shell('am start -a android.intent.action.VIEW -d "fb://marketplace"')
        human_pause(android_cfg, scale=1.1)
        xml = d.dump_hierarchy()
        if "Pages can't use Marketplace" in xml:
            raise RuntimeError(
                "Marketplace недоступен: переключитесь вручную на личный профиль Facebook"
            )
    if d(text="Продать").exists(timeout=3) or d(description="Продать").exists(timeout=0.5):
        return "marketplace_ready"
    raise RuntimeError("Не удалось открыть Facebook Marketplace")


def _open_property_form(d, android_cfg: dict[str, Any]) -> None:
    label = "Продажа и аренда недвижимости"
    for attempt in range(3):
        if not (
            d(text=label).exists(timeout=0.8)
            or d(description=label).exists(timeout=0.4)
        ):
            if d(description="Продать").exists(timeout=2):
                d(description="Продать").click()
            elif d(text="Продать").exists(timeout=1):
                d(text="Продать").click()
            else:
                raise RuntimeError("Кнопка «Продать» не найдена")
            human_pause(android_cfg, scale=0.9)
        if d(text=label).exists(timeout=5):
            d(text=label).click()
            break
        if d(description=label).exists(timeout=1):
            d(description=label).click()
            break
        # sheet не открылся — назад и ещё раз
        d.press("back")
        time.sleep(0.8)
        if attempt == 2:
            raise RuntimeError(f"Пункт «{label}» не найден")
    human_pause(android_cfg, scale=1.2)
    for _ in range(25):
        if (
            d(textContains="Новое объявление").exists(timeout=0.4)
            or d(text="Продажа/аренда недвижимости").exists(timeout=0.3)
            or d(text="Добавить фото").exists(timeout=0.3)
            or d(text="Сдается в аренду").exists(timeout=0.3)
        ):
            return
        # иногда сразу черновик / продолжение
        if d(text="СОХРАНИТЬ ЧЕРНОВИК").exists(timeout=0.3):
            d.press("back")
            time.sleep(0.5)
        time.sleep(0.4)
    raise RuntimeError("Форма объявления о недвижимости не открылась")


def _set_rent(d, android_cfg: dict[str, Any]) -> None:
    if d(text="Сдается в аренду").exists(timeout=1):
        return
    if not (
        _tap_desc_contains(d, "Задать значение: Продажа/аренда")
        or _tap_desc_contains(d, "Продажа/аренда недвижимости")
    ):
        raise RuntimeError("Не удалось открыть Продажа/аренда")
    human_pause(android_cfg, scale=0.6)
    if d(text="Сдается в аренду").exists(timeout=3):
        d(text="Сдается в аренду").click()
    elif not _tap_desc_contains(d, "Сдается в аренду"):
        raise RuntimeError("Не выбрано «Сдается в аренду»")
    human_pause(android_cfg, scale=0.6)


def _set_property_type(d, android_cfg: dict[str, Any], housing_type: str | None) -> str:
    wanted = _map_property_type(housing_type)
    current = ""
    xml = d.dump_hierarchy()
    m = re.search(r'content-desc="Тип объекта, ([^,]*)', xml)
    if m:
        current = (m.group(1) or "").strip()
    if current == wanted:
        return wanted
    opened = (
        _tap_desc_contains(d, "Задать значение: Тип объекта")
        or _tap_desc_contains(d, "Задать значение: Тип недвижимости")
    )
    if not opened and d(text="Тип объекта").exists(timeout=1):
        d(text="Тип объекта").click()
        opened = True
    if not opened and d(text="Тип недвижимости").exists(timeout=0.5):
        d(text="Тип недвижимости").click()
        opened = True
    if not opened:
        raise RuntimeError("Не удалось открыть тип объекта")
    human_pause(android_cfg, scale=0.6)
    if d(text=wanted).exists(timeout=3):
        d(text=wanted).click()
    else:
        raise RuntimeError(f"Тип «{wanted}» не найден в пикере")
    human_pause(android_cfg, scale=0.5)
    return wanted


def _set_edit_field(d, desc_substr: str, value: str) -> None:
    if not value:
        return
    if not _tap_desc_contains(d, desc_substr):
        if d(text=desc_substr).exists(timeout=0.8):
            d(text=desc_substr).click()
        elif d(textContains=desc_substr).exists(timeout=0.5):
            d(textContains=desc_substr).click()
        else:
            return
    time.sleep(0.4)
    # AdbKeyboard clear_text часто падает — set_text без clear
    typed = False
    if d(className="android.widget.EditText").exists(timeout=1.5):
        try:
            d(className="android.widget.EditText").set_text(value)
            typed = True
        except Exception:
            pass
    if not typed:
        try:
            d.send_keys(value)
            typed = True
        except Exception:
            if d(focused=True).exists(timeout=0.5):
                try:
                    d(focused=True).set_text(value)
                    typed = True
                except Exception:
                    pass
    time.sleep(0.35)
    try:
        d.hide_keyboard()
    except Exception:
        pass
    time.sleep(0.25)


_BAD_LOCATION_MARKERS = (
    "hong kong",
    "гонконг",
    "hongkong",
    "china",
    "кита",
    "singapore",
    "bangkok",
    "бангкок",
)


def _is_bad_location(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _BAD_LOCATION_MARKERS)


def _is_phuket_location(text: str) -> bool:
    low = (text or "").lower()
    if _is_bad_location(low):
        return False
    return any(
        k in low
        for k in (
            "phuket",
            "пхукет",
            "thalang",
            "тхаланг",
            "bang tao",
            "bang thao",
            "банг тао",
            "cherng",
            "kamala",
            "amphoe",
            "ภูเก็ต",
            "ถลาง",
            "เชิงทะเล",
        )
    )


def _set_address(
    d,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
    job: PublishJob,
) -> str:
    """Адрес обязателен: пикер «Выбор адреса» → клик по Button-подсказке (не EditText)."""
    listing = job.listing
    mp = _mp_cfg(publisher_cfg, android_cfg)
    fallback = (mp.get("address_fallback") or "Amphoe Thalang").strip()
    # Запросы, которые реально дают список Button-подсказок в RU Facebook
    search_queries = [
        q
        for q in (
            "Thalang Phuket",
            mp.get("address_search"),
            f"{listing.district} Phuket" if listing.district else None,
            "Amphoe Thalang Phuket",
            fallback,
            "Phuket Thalang",
        )
        if q
    ]

    opened = False
    for _ in range(8):
        if d(descriptionContains="Задать значение: Адрес").exists(timeout=0.6):
            d(descriptionContains="Задать значение: Адрес").click()
            opened = True
            break
        opened = (
            _tap_desc_contains(d, "Задать значение: Адрес")
            or _tap_desc_contains(d, "Адрес сдаваемого жилья")
            or _tap_desc_contains(d, "Адрес объекта недвижимости")
        )
        if not opened:
            for label in (
                "Адрес сдаваемого жилья",
                "Адрес объекта недвижимости",
                "Адрес объекта",
            ):
                if d(text=label).exists(timeout=0.4):
                    d(text=label).click()
                    opened = True
                    break
        if opened:
            break
        d.swipe(0.5, 0.60, 0.5, 0.48, 0.25)
        time.sleep(0.35)
    if not opened:
        return ""

    human_pause(android_cfg, scale=0.6)
    if not d(text="Выбор адреса").exists(timeout=2):
        # уже выбран ранее
        return "already_set"

    def _type_query(q: str) -> None:
        if not d(className="android.widget.EditText").exists(timeout=2):
            return
        et = d(className="android.widget.EditText")
        et.click()
        time.sleep(0.25)
        try:
            et.set_text(q)
        except Exception:
            try:
                d.send_keys(q)
            except Exception:
                pass

    def _suggestion_buttons() -> list[tuple[int, int, int, int, str]]:
        """Кликабельные Button под строкой поиска (тайские/EN адреса)."""
        xml = d.dump_hierarchy()
        out: list[tuple[int, int, int, int, str]] = []
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
            if not label or len(label) < 4:
                continue
            if label in ("Закрыть", "Назад", "Домой", "Последние", "Выбор адреса"):
                continue
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            if y1 < 240 or y1 > 1450:
                continue
            if not _is_phuket_location(label):
                continue
            if _is_bad_location(label):
                continue
            out.append((y1, x1, x2, y2, label))
        out.sort()
        return out

    def _confirm_left_picker() -> bool:
        return not d(text="Выбор адреса").exists(timeout=0.8)

    chosen = ""
    for q in search_queries:
        _type_query(q)
        time.sleep(2.2)
        buttons = _suggestion_buttons()
        if not buttons:
            continue
        y1, x1, x2, y2, label = buttons[0]
        d.click((x1 + x2) // 2, (y1 + y2) // 2)
        human_pause(android_cfg, scale=0.8)
        if _confirm_left_picker():
            chosen = label
            break
        # повторный тап по той же подсказке
        d.click((x1 + x2) // 2, (y1 + y2) // 2)
        time.sleep(1.0)
        if _confirm_left_picker():
            chosen = label
            break

    if not chosen or not _confirm_left_picker():
        raise RuntimeError(
            f"Не удалось выбрать адрес на Пхукете (подсказка Button, не поле ввода). "
            f"Пробовали: {', '.join(search_queries[:4])}."
        )
    return chosen


def _add_photos(
    d,
    android_cfg: dict[str, Any],
    *,
    album: str,
    max_images: int,
) -> int:
    max_images = max(1, min(int(max_images), 20))
    if d(text="Добавить фото").exists(timeout=2):
        d(text="Добавить фото").click()
    elif d(description="Добавить фото").exists(timeout=1):
        d(description="Добавить фото").click()
    else:
        raise RuntimeError("«Добавить фото» не найдено")
    time.sleep(1.2)
    dismiss_permissions(d)

    def _photo_tiles() -> list[tuple[int, int, int, int]]:
        xml = d.dump_hierarchy()
        out: list[tuple[int, int, int, int]] = []
        for node in ET.fromstring(xml).iter("node"):
            a = node.attrib
            if a.get("clickable") != "true":
                continue
            desc = (a.get("content-desc") or "").lower()
            if "сделать" in desc or "camera" in desc or "камер" in desc:
                continue
            if "фото" not in desc and "photo" not in desc:
                continue
            parsed = _parse_bounds(a.get("bounds", ""))
            if not parsed:
                continue
            x1, y1, x2, y2 = parsed
            if y1 < 210 or (x2 - x1) < 80:
                continue
            out.append((y1, x1, (x1 + x2) // 2, (y1 + y2) // 2))
        out.sort()
        return out

    tiles = _photo_tiles()
    # Уже сетка с фото (часто «Галерея» = заголовок) — не кликать заголовок.
    # Альбом нужен только если сетки нет или открыт radio-list альбомов.
    on_album_list = d(text="Telegram").exists(timeout=0.4) or (
        d(text=album).exists(timeout=0.3) and d(text="Галерея").exists(timeout=0.3) and len(tiles) < 2
    )
    if on_album_list or len(tiles) < 2:
        if d(text="Галерея").exists(timeout=0.8) and not on_album_list:
            d(text="Галерея").click()
            time.sleep(1.0)
        if d(descriptionContains=album).exists(timeout=2):
            d(descriptionContains=album).click()
        elif d(text=album).exists(timeout=0.5):
            _tap_desc_contains(d, album)
        time.sleep(0.5)
        # radio-list → назад в сетку выбранного альбома
        if d(text="Telegram").exists(timeout=0.5) or d(descriptionContains="Telegram").exists(
            timeout=0.3
        ):
            d.press("back")
            time.sleep(1.0)
        tiles = _photo_tiles()

    if len(tiles) < 1:
        raise RuntimeError(
            f"В альбоме «{album}» нет фото. Сначала: prepare --push-media"
        )

    selected = 0
    for _, __, cx, cy in tiles[:max_images]:
        d.click(cx, cy)
        selected += 1
        time.sleep(0.5)

    confirmed = False
    for _ in range(20):
        if d(description="Далее").exists(timeout=0.5):
            d(description="Далее").click()
            confirmed = True
            break
        if d(text="Далее").exists(timeout=0.3):
            d(text="Далее").click()
            confirmed = True
            break
        for label in ("Готово", "Done", "Добавить"):
            if d(text=label).exists(timeout=0.2) or d(description=label).exists(timeout=0.2):
                (d(text=label) if d(text=label).exists() else d(description=label)).click()
                confirmed = True
                break
        if confirmed:
            break
        time.sleep(0.35)
    if not confirmed:
        raise RuntimeError("После выбора фото нет «Далее»/«Готово»")
    human_pause(android_cfg, scale=1.0)
    return selected


def _scroll_to_top(d, n: int = 4) -> None:
    for _ in range(n):
        d.swipe(0.5, 0.28, 0.5, 0.82, 0.25)
        time.sleep(0.3)


def _scroll_down(d, n: int = 2) -> None:
    for _ in range(n):
        d.swipe(0.5, 0.75, 0.5, 0.35, 0.3)
        time.sleep(0.35)


def _assert_form_not_limited(d) -> None:
    xml = d.dump_hierarchy()
    if "Достигнуто ограничение" in xml or (
        "временно ограничено" in xml and "Marketplace" in xml
    ):
        raise RuntimeError(
            "Facebook ограничил публикацию в Marketplace (новый продавец / лимит объявлений). "
            "Подождите или снимите лимит в приложении, затем повторите."
        )


def _fill_form(
    d,
    job: PublishJob,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
    *,
    album: str | None = None,
    max_images: int | None = None,
) -> dict[str, Any]:
    cfg = _mp_cfg(publisher_cfg, android_cfg)
    album = album or cfg.get("album") or ALBUM_DEFAULT
    max_images = int(
        max_images
        or cfg.get("max_images")
        or (publisher_cfg.get("media") or {}).get("max_marketplace_images")
        or 8
    )
    listing = job.listing
    meta: dict[str, Any] = {}

    _scroll_to_top(d)
    _assert_form_not_limited(d)
    _set_rent(d, android_cfg)
    meta["rent"] = "Сдается в аренду"
    _assert_form_not_limited(d)
    meta["type"] = _set_property_type(d, android_cfg, listing.housing_type)

    beds = _format_rooms(listing.rooms)
    baths = _format_rooms(listing.bathrooms)
    price = _format_price(listing.price_monthly)
    if beds:
        _set_edit_field(d, "Количество спален", beds)
        meta["beds"] = beds
    if baths:
        _set_edit_field(d, "Количество санузлов", baths)
        meta["baths"] = baths
    if price:
        _set_edit_field(d, "Цена за месяц", price)
        meta["price"] = price

    # Адрес обычно уже на экране сразу под ценой — не скроллить вниз (поле уезжает)
    addr = _set_address(d, android_cfg, publisher_cfg, job)
    if addr:
        meta["address"] = addr
    else:
        raise RuntimeError("Не удалось указать адрес объекта (обязательное поле)")

    caption = (job.caption_fb or job.caption_social or "").strip()
    if caption:
        if d(text="Выбор адреса").exists(timeout=0.3):
            raise RuntimeError("Остались на «Выбор адреса» — адрес не подтверждён")
        _set_listing_description(d, caption, android_cfg)
        meta["caption_len"] = len(caption)

    _scroll_to_top(d)
    if d(text="Выбор адреса").exists(timeout=0.3):
        raise RuntimeError("Перед фото снова экран «Выбор адреса»")
    n = _add_photos(d, android_cfg, album=album, max_images=max_images)
    meta["photos"] = n
    return meta


def _set_listing_description(d, caption: str, android_cfg: dict[str, Any]) -> None:
    """
    Пишем ТОЛЬКО в EditText «Описание объекта недвижимости».
    Не кликать лейбл/удобства; не send_keys вслепую в «Поиск».
    """
    caption = (caption or "").strip()[:8000]
    if not caption:
        return

    # если в Поиске удобств уже длинный текст — очистить
    if d(description="Поиск").exists(timeout=0.4):
        try:
            info = d(description="Поиск").info or {}
            if len((info.get("text") or "")) > 20:
                d(description="Поиск").set_text("")
        except Exception:
            pass

    el = None
    for _ in range(8):
        cand = d(
            className="android.widget.EditText",
            descriptionContains="Описание объекта недвижимости",
        )
        if cand.exists(timeout=0.55):
            el = cand
            break
        d.swipe(0.5, 0.62, 0.5, 0.45, 0.25)
        time.sleep(0.3)
    if el is None:
        raise RuntimeError("EditText «Описание объекта недвижимости» не найден")

    el.click()
    time.sleep(0.35)
    try:
        el.set_text(caption)
    except Exception:
        # повторный хендл после возможного рефреша UI
        el2 = d(
            className="android.widget.EditText",
            descriptionContains="Описание объекта недвижимости",
        )
        if not el2.exists(timeout=1):
            raise RuntimeError("Потеряли поле описания после клика")
        el2.set_text(caption)
    time.sleep(0.45)
    try:
        d.hide_keyboard()
    except Exception:
        pass

    xml = d.dump_hierarchy()
    # провал: длинный текст оказался в Поиске удобств
    for node in ET.fromstring(xml).iter("node"):
        a = node.attrib
        if (a.get("content-desc") or "").strip() != "Поиск":
            continue
        t = a.get("text") or ""
        if len(t) > 40:
            try:
                d(description="Поиск").set_text("")
            except Exception:
                pass
            raise RuntimeError(
                "Описание попало в «Дополнительные удобства»/Поиск — очищено"
            )

    m = re.search(
        r'content-desc="Описание объекта недвижимости,\s*([^"]{8,})',
        xml,
    )
    if not m or m.group(1).strip() in (",", ""):
        # пустое описание
        if 'content-desc="Описание объекта недвижимости, , ,' in xml:
            raise RuntimeError(
                "Описание не записалось в «Описание объекта недвижимости»"
            )
    human_pause(android_cfg, scale=0.3)


def _go_to_publish_screen(d, android_cfg: dict[str, Any]) -> None:
    # с формы → экран групп / Опубликовать
    for attempt in range(5):
        if d(description="Опубликовать").exists(timeout=1) or d(text="Опубликовать").exists(
            timeout=0.5
        ):
            return
        xml = d.dump_hierarchy()
        # незаполненные обязательные поля
        if "Укажите адрес" in xml or "Добавьте описание" in xml:
            raise RuntimeError(
                "Форма не полная (нужны описание и/или адрес) — не удалось перейти к публикации"
            )
        if d(description="Далее").exists(timeout=1):
            d(description="Далее").click()
        elif d(text="Далее").exists(timeout=0.5):
            d(text="Далее").click()
        else:
            break
        human_pause(android_cfg, scale=1.0)
        # на экране групп кнопка может быть ниже
        for _ in range(3):
            if d(description="Опубликовать").exists(timeout=0.8) or d(
                text="Опубликовать"
            ).exists(timeout=0.4):
                return
            d.swipe(0.5, 0.75, 0.5, 0.35, 0.3)
            time.sleep(0.45)
    if not (
        d(description="Опубликовать").exists(timeout=1)
        or d(text="Опубликовать").exists(timeout=0.5)
    ):
        raise RuntimeError("Экран «Опубликовать» не найден")


def _publish_or_stop(
    d,
    *,
    confirm_post: bool,
    android_cfg: dict[str, Any],
    channel: str = "fb_marketplace",
) -> ChannelResult:
    if not confirm_post:
        # безопасный стоп: назад → сохранить черновик, если диалог есть
        d.press("back")
        time.sleep(1.0)
        if d(text="СОХРАНИТЬ ЧЕРНОВИК").exists(timeout=2):
            d(text="СОХРАНИТЬ ЧЕРНОВИК").click()
            human_pause(android_cfg)
            return ChannelResult(
                channel=channel,
                ok=True,
                skipped=True,
                reason="stopped_before_publish",
                note="Форма заполнена → Черновик (--live для Опубликовать)",
            )
        return ChannelResult(
            channel=channel,
            ok=True,
            skipped=True,
            reason="stopped_before_publish",
            note="Дошли до экрана публикации, «Опубликовать» не нажат (--live)",
        )

    if d(description="Опубликовать").exists(timeout=2):
        d(description="Опубликовать").click()
    elif d(text="Опубликовать").exists(timeout=1):
        d(text="Опубликовать").click()
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
        note="Нажато «Опубликовать» — дождитесь загрузки",
    )


class FbMarketplaceChannel:
    name = "fb_marketplace"

    def publish(
        self,
        job: PublishJob,
        *,
        dry_run: bool,
        android_cfg: dict[str, Any],
        publisher_cfg: dict[str, Any],
        confirm_post: bool = False,
    ) -> ChannelResult:
        # Только brand_open_home — не carousel / не колонка «Фото»
        images = (
            job.device_marketplace_images
            or job.local_marketplace_images
            or job.marketplace_image_urls
        )
        if not images:
            return ChannelResult(
                channel=self.name,
                ok=False,
                skipped=True,
                reason="no marketplace images (brand_open_home_url)",
            )

        package = _package(android_cfg)
        listing = job.listing
        mp_cfg = _mp_cfg(publisher_cfg, android_cfg)
        album = mp_cfg.get("album") or "brand_open_home"
        note = (
            f"FB MP rent: brand_images={len(images)}; album={album}; "
            f"price={listing.price_monthly}; rooms={listing.rooms}; "
            f"type={listing.housing_type!r}; caption_len={len(job.caption_fb or '')}"
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
            from ..android.ui import ensure_unlocked

            d = connect_device(android_cfg)
            d.screen_on()
            ensure_unlocked(d)
            for extra in (
                "com.google.android.photopicker",
                "com.google.android.providers.media.module",
            ):
                try:
                    d.app_stop(extra)
                except Exception:
                    pass
            d.press("home")
            time.sleep(0.4)
            d.app_stop(package)
            time.sleep(0.55)
            d.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
            human_pause(android_cfg, scale=1.3)
            ensure_unlocked(d)
            dismiss_permissions(d)

            _ensure_personal_profile(d, android_cfg)
            _open_property_form(d, android_cfg)
            meta = _fill_form(
                d,
                job,
                android_cfg,
                publisher_cfg,
                album=album,
                max_images=min(len(images), int(mp_cfg.get("max_images") or 8)),
            )
            note = f"{note}; filled={meta}"
            _go_to_publish_screen(d, android_cfg)
            result = _publish_or_stop(
                d, confirm_post=confirm_post, android_cfg=android_cfg
            )
            # ссылку на объявление Marketplace не копируем — не нужна
            result.note = f"{(result.note or '')}; {note}".strip("; ")
            return result
        except Exception as e:
            return ChannelResult(
                channel=self.name,
                ok=False,
                reason=str(e),
                note=note,
            )
