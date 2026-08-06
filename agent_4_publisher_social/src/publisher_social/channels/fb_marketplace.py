from __future__ import annotations

import re
import time
from typing import Any
from xml.etree import ElementTree as ET

from ..android.ui import connect_device, dismiss_permissions, human_pause
from ..maps_location import marketplace_location_query
from ..marketplace.session import MarketplaceSafeStop, MarketplaceSession
from ..models import PublishJob
from .base import ChannelResult, count_selected_gallery_photos, gallery_selection_state, marketplace_folder_name
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


_SELL_LABELS = ("Продать", "Выставить на продажу")
_CREATE_LISTING_LABELS = ("Создать новое объявление", "Создать объявление")
_RENT_KIND_LABELS = ("Аренда", "Аренда жилья", "Сдается в аренду")
_PROPERTY_CATEGORY_LABELS = (
    "Продажа и аренда недвижимости",
    "Продажи и аренда жилья",
    "Продажа или аренда жилья",
)
_MAX_MARKETPLACE_PHOTOS = 49
# Без скролла в галерее — только видимая сетка (обычно 3×5 + частичный ряд).
_VISIBLE_MARKETPLACE_PHOTOS = 16
_PHOTO_TILE_MIN_Y = 130  # статус-бар; верхний ряд фото начинается ~155px


def _label_visible(d, labels: tuple[str, ...], *, timeout: float = 0.8) -> str | None:
    for index, label in enumerate(labels):
        wait = timeout if index == 0 else 0.4
        if d(text=label).exists(timeout=wait) or d(description=label).exists(timeout=0.4):
            return label
    return None


def _click_label(d, label: str) -> None:
    if d(description=label).exists(timeout=2):
        d(description=label).click()
        return
    if d(text=label).exists(timeout=1):
        d(text=label).click()
        return
    raise RuntimeError(f"Кнопка «{label}» не найдена")


def _open_create_listing_if_needed(d, android_cfg: dict[str, Any]) -> None:
    if _is_composer_form(d) or _listing_category_sheet_visible(d):
        return
    create = _label_visible(d, _CREATE_LISTING_LABELS, timeout=2)
    if create:
        _click_label(d, create)
        human_pause(android_cfg, scale=0.9)
        return
    if _sell_button_visible(d, timeout=1):
        _click_sell_button(d)
        human_pause(android_cfg, scale=0.9)
    create = _label_visible(d, _CREATE_LISTING_LABELS, timeout=3)
    if create:
        _click_label(d, create)
        human_pause(android_cfg, scale=0.9)


_HOUSING_KIND_LABELS = (
    "Продажа или аренда жилья",
    "Продажи и аренда жилья",
    *_PROPERTY_CATEGORY_LABELS,
)


def _listing_category_sheet_visible(d) -> bool:
    return bool(
        d(resourceId="composer_bottom_row_housing").exists(timeout=0.5)
        or _label_visible(d, _HOUSING_KIND_LABELS, timeout=0.5)
    )


def _select_housing_listing_kind(d, android_cfg: dict[str, Any]) -> None:
    """Нижний пункт листа «Создать объявление» — жильё."""
    if _is_composer_form(d):
        return
    if d(resourceId="composer_bottom_row_housing").exists(timeout=2):
        d(resourceId="composer_bottom_row_housing").click()
        human_pause(android_cfg, scale=1.0)
        return
    label = _label_visible(d, _HOUSING_KIND_LABELS, timeout=3)
    if label:
        _click_label(d, label)
        human_pause(android_cfg, scale=1.0)
        return
    raise RuntimeError("Не найден пункт «Продажа или аренда жилья»")


def _select_rent_listing_kind(d, android_cfg: dict[str, Any]) -> None:
    """После «Создать объявление» — выбрать «Аренда» (нижний пункт «Продажи / Аренда»)."""
    if _is_composer_form(d):
        return
    candidates: list[tuple[int, int, int]] = []
    xml = d.dump_hierarchy()
    for node in ET.fromstring(xml).iter("node"):
        attrs = node.attrib
        if attrs.get("clickable") != "true":
            continue
        label = (attrs.get("text") or attrs.get("content-desc") or "").strip()
        if not label:
            continue
        if not any(kind in label for kind in _RENT_KIND_LABELS):
            continue
        if "Продаж" in label and "Аренд" not in label:
            continue
        parsed = _parse_bounds(attrs.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        candidates.append((y1, (x1 + x2) // 2, (y1 + y2) // 2))
    if candidates:
        candidates.sort(reverse=True)
        _, cx, cy = candidates[0]
        d.click(cx, cy)
        human_pause(android_cfg, scale=1.0)
        return
    for label in _RENT_KIND_LABELS:
        if d(description=label).exists(timeout=1):
            d(description=label).click()
            human_pause(android_cfg, scale=1.0)
            return
        if d(text=label).exists(timeout=0.8):
            d(text=label).click()
            human_pause(android_cfg, scale=1.0)
            return
    raise RuntimeError("Не найден пункт «Аренда» после «Создать объявление»")


def _open_listing_form(d, android_cfg: dict[str, Any]) -> None:
    """Выставить на продажу → Создать объявление → Аренда → форма «Новое объявление»."""
    if _is_composer_form(d):
        return
    _open_create_listing_if_needed(d, android_cfg)
    if not _is_composer_form(d):
        _select_housing_listing_kind(d, android_cfg)
    if not _is_composer_form(d):
        _select_rent_listing_kind(d, android_cfg)
    for _ in range(25):
        if _is_composer_form(d):
            return
        if d(text="Добавить фото").exists(timeout=0.3) or d(
            description="Добавить фото"
        ).exists(timeout=0.2):
            return
        time.sleep(0.4)
    raise RuntimeError("Форма «Новое объявление» не открылась")


def _sell_button_visible(d, *, timeout: float = 3) -> bool:
    for label in _SELL_LABELS:
        if d(text=label).exists(timeout=timeout if label == _SELL_LABELS[0] else 0.5):
            return True
        if d(description=label).exists(timeout=0.5):
            return True
    return False


def _click_sell_button(d) -> None:
    for label in _SELL_LABELS:
        if d(description=label).exists(timeout=2):
            d(description=label).click()
            return
        if d(text=label).exists(timeout=1):
            d(text=label).click()
            return
    raise RuntimeError("Кнопка «Продать» / «Выставить на продажу» не найдена")


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
    if _sell_button_visible(d):
        return "marketplace_ready"
    raise RuntimeError("Не удалось открыть Facebook Marketplace")


def _open_property_form(d, android_cfg: dict[str, Any]) -> None:
    """Legacy property picker; основной путь — _open_listing_form (composer)."""
    _open_listing_form(d, android_cfg)
    if _is_composer_form(d):
        return
    for attempt in range(3):
        category = _label_visible(d, _PROPERTY_CATEGORY_LABELS)
        if not category:
            _open_create_listing_if_needed(d, android_cfg)
            category = _label_visible(d, _PROPERTY_CATEGORY_LABELS, timeout=5)
        if category:
            _click_label(d, category)
            break
        d.press("back")
        time.sleep(0.8)
        if attempt == 2:
            raise RuntimeError(
                "Пункт «Продажа и аренда недвижимости» / «Продажи и аренда жилья» не найден"
            )
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


def _is_composer_form(d) -> bool:
    return bool(
        d(resourceId="mp_composer_view").exists(timeout=0.5)
        or d(textContains="Новое объявление").exists(timeout=0.3)
    )


def _composer_location_ok(d) -> bool:
    xml = d.dump_hierarchy()
    if "Phuket" not in xml and "Пхукет" not in xml and "Thalang" not in xml:
        return False
    for node in ET.fromstring(xml).iter("node"):
        text = (node.attrib.get("text") or "").strip()
        if text and _is_phuket_location(text):
            return True
    return False


def _hide_keyboard(d) -> None:
    try:
        d.hide_keyboard()
    except Exception:
        pass
    time.sleep(0.2)


def _tile_key(x1: int, y1: int, x2: int, y2: int) -> str:
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    return f"{cx // 40}:{cy // 40}"


def _composer_edittext_nodes(d) -> list[dict[str, Any]]:
    xml = d.dump_hierarchy()
    nodes: list[dict[str, Any]] = []
    for node in ET.fromstring(xml).iter("node"):
        if node.attrib.get("class") != "android.widget.EditText":
            continue
        parsed = _parse_bounds(node.attrib.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        nodes.append(
            {
                "resource_id": node.attrib.get("resource-id") or "",
                "desc": node.attrib.get("content-desc") or "",
                "y1": y1,
                "bounds": parsed,
            }
        )
    nodes.sort(key=lambda item: item["y1"])
    return nodes


def _click_and_set_text(d, x: int, y: int, value: str) -> None:
    d.click(x, y)
    time.sleep(0.3)
    if d(focused=True).exists(timeout=1):
        d(focused=True).set_text(value)
    else:
        d.send_keys(value)


def _apply_field_text(d, validation, value: str) -> None:
    from ..marketplace.validator import FieldValidation

    if not isinstance(validation, FieldValidation) or validation.match is None:
        raise RuntimeError("semantic validation missing for field")
    node = validation.match.node
    if node.resource_id and d(resourceId=node.resource_id).exists(timeout=0.8):
        field = d(resourceId=node.resource_id)
        field.click()
        time.sleep(0.2)
        field.set_text(value)
        return
    if not node.bounds:
        raise RuntimeError("field bounds missing")
    x1, y1, x2, y2 = node.bounds
    _click_and_set_text(d, (x1 + x2) // 2, (y1 + y2) // 2, value)


def _set_composer_title(d, title: str, session: MarketplaceSession | None = None) -> None:
    title = (title or "").strip()[:100]
    if not title:
        return
    if session is not None:
        session.guarded_fill_field(
            "title",
            title,
            set_text=lambda validation, value: _apply_field_text(d, validation, value),
            read_back=lambda: _read_composer_title(d),
        )
        return
    if d(descriptionContains="Название").exists(timeout=1):
        d(descriptionContains="Название").click()
        time.sleep(0.3)
    nodes = _composer_edittext_nodes(d)
    title_node = next(
        (n for n in nodes if n["resource_id"] != "marketplace_composer_price_input"),
        None,
    )
    if not title_node:
        raise RuntimeError("Поле «Название» не найдено")
    x1, y1, x2, y2 = title_node["bounds"]
    _click_and_set_text(d, (x1 + x2) // 2, (y1 + y2) // 2, title)
    _hide_keyboard(d)


def _read_composer_title(d) -> str:
    nodes = _composer_edittext_nodes(d)
    title_node = next(
        (n for n in nodes if n["resource_id"] != "marketplace_composer_price_input"),
        None,
    )
    if not title_node:
        return ""
    if d(resourceId=title_node["resource_id"]).exists(timeout=0.3):
        try:
            return str(d(resourceId=title_node["resource_id"]).get_text() or "").strip()
        except Exception:
            pass
    return ""


def _set_composer_price(d, price: str, session: MarketplaceSession | None = None) -> None:
    price = (price or "").strip()
    if not price:
        return
    if session is not None:
        session.guarded_fill_field(
            "price",
            price,
            value_type="numeric",
            set_text=lambda validation, value: _apply_field_text(d, validation, value),
            read_back=lambda: _composer_price_value(d),
        )
        return
    if d(descriptionContains="Цена").exists(timeout=0.8):
        d(descriptionContains="Цена").click()
        time.sleep(0.2)
    if d(resourceId="marketplace_composer_price_input").exists(timeout=1):
        field = d(resourceId="marketplace_composer_price_input")
        field.click()
        time.sleep(0.25)
        field.set_text(price)
    else:
        nodes = _composer_edittext_nodes(d)
        price_node = next(
            (n for n in nodes if n["resource_id"] == "marketplace_composer_price_input"),
            nodes[-1] if nodes else None,
        )
        if not price_node:
            raise RuntimeError("Поле «Цена» не найдено")
        x1, y1, x2, y2 = price_node["bounds"]
        _click_and_set_text(d, (x1 + x2) // 2, (y1 + y2) // 2, price)
    _hide_keyboard(d)


def _composer_price_value(d) -> str:
    if not d(resourceId="marketplace_composer_price_input").exists(timeout=0.5):
        return ""
    try:
        return str(d(resourceId="marketplace_composer_price_input").get_text() or "").strip()
    except Exception:
        return ""


def _is_tags_field_node(desc: str, y1: int) -> bool:
    low = (desc or "").lower()
    if "тег" in low or "tag" in low:
        return True
    return y1 >= 960


def _clear_composer_tags_if_polluted(d, *, min_len: int = 30) -> None:
    for node in _composer_edittext_nodes(d):
        if not _is_tags_field_node(node["desc"], node["y1"]):
            continue
        x1, y1, x2, y2 = node["bounds"]
        d.click((x1 + x2) // 2, (y1 + y2) // 2)
        time.sleep(0.25)
        if d(focused=True).exists(timeout=0.5):
            try:
                current = str(d(focused=True).get_text() or "")
            except Exception:
                current = ""
            if len(current) >= min_len:
                d(focused=True).set_text("")
        break


def _find_composer_description_edittext(d) -> dict[str, Any] | None:
    for node in _composer_edittext_nodes(d):
        if node["resource_id"] == "marketplace_composer_price_input":
            continue
        if _is_tags_field_node(node["desc"], node["y1"]):
            continue
        if "Описание" in node["desc"] or 620 <= node["y1"] <= 950:
            return node
    return None


def _open_composer_description_field(d) -> dict[str, Any]:
    _hide_keyboard(d)
    _scroll_down(d, 1)
    time.sleep(0.3)

    for label in ("Описание  Необязательно", "Описание"):
        if d(description=label).exists(timeout=0.8):
            d(description=label).click()
            time.sleep(0.35)
            node = _find_composer_description_edittext(d)
            if node:
                return node

    xml = d.dump_hierarchy() or ""
    for node in ET.fromstring(xml).iter("node"):
        desc = (node.attrib.get("content-desc") or "").strip()
        if not desc.startswith("Описание"):
            continue
        if _is_tags_field_node(desc, 0):
            continue
        if "Категория" in desc or "Название" in desc or "Теги" in desc:
            continue
        parsed = _parse_bounds(node.attrib.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        if y1 >= 960:
            continue
        d.click((x1 + x2) // 2, (y1 + y2) // 2)
        time.sleep(0.35)
        found = _find_composer_description_edittext(d)
        if found:
            return found

    for _ in range(3):
        _scroll_down(d, 1)
        time.sleep(0.3)
        found = _find_composer_description_edittext(d)
        if found:
            x1, y1, x2, y2 = found["bounds"]
            d.click((x1 + x2) // 2, (y1 + y2) // 2)
            time.sleep(0.35)
            return found

    raise RuntimeError("Поле «Описание» не найдено в composer")


def _set_composer_description(
    d,
    caption: str,
    android_cfg: dict[str, Any],
    session: MarketplaceSession | None = None,
) -> None:
    caption = (caption or "").strip()[:8000]
    if not caption:
        return

    if session is not None:
        _clear_composer_tags_if_polluted(d)
        session.guarded_fill_field(
            "description",
            caption,
            set_text=lambda validation, value: _apply_field_text(d, validation, value),
            read_back=lambda: _read_composer_description(d),
        )
        human_pause(android_cfg, scale=0.5)
        return

    _clear_composer_tags_if_polluted(d)
    desc_node = _open_composer_description_field(d)
    x1, y1, x2, y2 = desc_node["bounds"]
    _click_and_set_text(d, (x1 + x2) // 2, (y1 + y2) // 2, caption)
    _hide_keyboard(d)

    # Проверка: длинный текст не должен остаться в «Теги».
    for node in _composer_edittext_nodes(d):
        if not _is_tags_field_node(node["desc"], node["y1"]):
            continue
        x1, y1, x2, y2 = node["bounds"]
        d.click((x1 + x2) // 2, (y1 + y2) // 2)
        time.sleep(0.2)
        if d(focused=True).exists(timeout=0.4):
            try:
                tags_text = str(d(focused=True).get_text() or "")
            except Exception:
                tags_text = ""
            if len(tags_text) > 40 and tags_text[:40] in caption:
                d(focused=True).set_text("")
                raise RuntimeError(
                    "Описание попало в «Теги» — поле очищено, повторите"
                )
        break

    human_pause(android_cfg, scale=0.5)


def _read_composer_description(d) -> str:
    node = _find_composer_description_edittext(d)
    if not node:
        return ""
    rid = node.get("resource_id") or ""
    if rid and d(resourceId=rid).exists(timeout=0.3):
        try:
            return str(d(resourceId=rid).get_text() or "").strip()
        except Exception:
            pass
    if d(focused=True).exists(timeout=0.2):
        try:
            return str(d(focused=True).get_text() or "").strip()
        except Exception:
            pass
    return ""


def _set_composer_field(d, *, description_substr: str, value: str) -> None:
    """Deprecated helper — use _set_composer_title / _set_composer_price."""
    if description_substr == "Название":
        _set_composer_title(d, value)
    elif description_substr == "Цена":
        _set_composer_price(d, value)
    else:
        raise RuntimeError(f"Неизвестное поле composer: {description_substr}")


def _on_location_map_screen(d) -> bool:
    xml = d.dump_hierarchy() or ""
    return (
        "Добавить местоположение" in xml
        or "Mapbox" in xml
        or "Обновить свое местоположение" in xml
    )


def _marketplace_location_query(job: PublishJob) -> str:
    return marketplace_location_query(
        google_maps_url=job.listing.google_maps,
        district_fallback=job.listing.district,
    )


def _fill_location_map_search(d, query: str, android_cfg: dict[str, Any]) -> str:
    if d(description="Поиск").exists(timeout=2):
        d(description="Поиск").click()
    elif d(text="Поиск").exists(timeout=1):
        d(text="Поиск").click()
    elif d(className="android.widget.EditText").exists(timeout=1):
        d(className="android.widget.EditText").click()
    else:
        raise RuntimeError("Поле «Поиск» на карте местоположения не найдено")

    time.sleep(0.35)
    if d(className="android.widget.EditText").exists(timeout=1):
        field = d(className="android.widget.EditText")
        field.click()
        time.sleep(0.25)
        try:
            field.clear_text()
        except Exception:
            field.set_text("")
        time.sleep(0.2)
        field.set_text(query)
    elif d(focused=True).exists(timeout=1):
        d(focused=True).set_text(query)
    else:
        d.send_keys(query)

    time.sleep(2.5)
    chosen = _pick_first_location_suggestion(d)
    if not chosen:
        prefix = query.split(",")[0].strip()
        if prefix and d(textContains=prefix[:24]).exists(timeout=0.8):
            d(textContains=prefix[:24]).click()
            chosen = query
        else:
            d.press("enter")
            time.sleep(1.0)
            chosen = query
    if not chosen:
        raise RuntimeError(f"Нет подсказок для района из Notion: {query[:120]}")

    time.sleep(0.5)
    if d(descriptionContains="Применить").exists(timeout=1):
        d(descriptionContains="Применить").click()
    elif d(text="Применить").exists(timeout=1):
        d(text="Применить").click()
    else:
        d.click(360, 1442)
    human_pause(android_cfg, scale=0.8)

    if _on_location_map_screen(d):
        d.click(360, 1442)
        time.sleep(1.2)
    if _on_location_map_screen(d):
        raise RuntimeError("Карта не закрылась после «Применить»")
    return chosen


def _set_composer_location(
    d,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
    job: PublishJob,
) -> str:
    """
    Composer: Местоположение → карта → Поиск → район из Google Maps (или «Район») → Применить.
    """
    query = _marketplace_location_query(job)
    if not query:
        raise RuntimeError(
            "Нет района: заполните «Google Maps» или колонку «Район» в Notion"
        )

    _hide_keyboard(d)
    if not _on_location_map_screen(d):
        current = _read_composer_location(d)
        district_key = query.split(",")[0].strip().lower()
        if current and district_key and district_key in current.lower():
            return current

        opened = False
        for _ in range(3):
            for label in (
                "Задать значение: Местоположение",
                "Местоположение",
            ):
                if d(descriptionContains=label).exists(timeout=0.6):
                    d(descriptionContains=label).click()
                    opened = True
                    break
                if _tap_desc_contains(d, label):
                    opened = True
                    break
            if opened:
                break
            _scroll_down(d, 1)
        if not opened and not _on_location_map_screen(d):
            raise RuntimeError("Поле «Местоположение» не найдено")
        human_pause(android_cfg, scale=0.6)

    return _fill_location_map_search(d, query, android_cfg)


def _fill_composer_form(
    d,
    job: PublishJob,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
    *,
    album: str | None = None,
    max_images: int | None = None,
    expected_photos: int | None = None,
    session: MarketplaceSession | None = None,
) -> dict[str, Any]:
    cfg = _mp_cfg(publisher_cfg, android_cfg)
    album = album or marketplace_folder_name(job.object_id)
    max_images = int(
        max_images
        or cfg.get("max_images")
        or (publisher_cfg.get("media") or {}).get("max_marketplace_images")
        or _MAX_MARKETPLACE_PHOTOS
    )
    if expected_photos is not None:
        max_images = min(max_images, int(expected_photos))
    listing = job.listing
    meta: dict[str, Any] = {"form": "composer", "album": album}

    if session is not None:
        session.refresh_snapshot()
        from ..marketplace.states import MarketplaceState

        if session.current_state == MarketplaceState.BLOCKED_CHECKPOINT:
            session.safe_stop(
                "blocked_checkpoint",
                publication_status="blocked_checkpoint",
            )

    _scroll_to_top_if_needed(d)
    _assert_form_not_limited(d)

    # Сначала фото, потом поля формы.
    meta["rent"] = "Аренда"
    photo_target = min(
        int(expected_photos or max_images),
        max_images,
        _VISIBLE_MARKETPLACE_PHOTOS,
    )
    selected, expected = _add_photos(
        d,
        android_cfg,
        album=album,
        max_images=max_images,
        expected_count=photo_target,
    )
    meta["photos"] = selected
    meta["photos_expected"] = expected
    if selected < expected:
        raise RuntimeError(
            f"Выбрано {selected} фото из {expected} в альбоме «{album}»"
        )

    title = (job.title or "").strip()
    if title:
        _set_composer_title(d, title, session=session)
        meta["title"] = title[:100]

    price = _format_price(listing.price_monthly)
    if price:
        _set_composer_price(d, price, session=session)
        meta["price"] = price
        if _composer_price_value(d) != price:
            _set_composer_price(d, price, session=session)

    addr = _set_composer_location(d, android_cfg, publisher_cfg, job)
    if not addr:
        raise RuntimeError("Не удалось указать местоположение")
    meta["address"] = addr

    caption = (job.caption_fb or "").strip()
    if caption:
        if _composer_price_value(d) != price and price:
            _set_composer_price(d, price, session=session)
        _set_composer_description(d, caption, android_cfg, session=session)
        meta["caption_len"] = len(caption)

    if session is not None:
        session.final_content_validation(
            title_value=_read_composer_title(d) or title,
            price_value=_composer_price_value(d) or price,
            description_value=_read_composer_description(d) or caption,
            media_count=int(meta.get("photos") or 0),
            expected_media_count=int(meta.get("photos_expected") or 0),
            location_value=str(meta.get("address") or ""),
        )

    return meta


def _set_rent(d, android_cfg: dict[str, Any]) -> None:
    if _is_composer_form(d):
        xml = d.dump_hierarchy()
        if "Аренда" in xml and (
            "Категория" in xml or "marketplace_composer_category" in xml
        ):
            return
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
    if _is_composer_form(d):
        return wanted
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


def _address_search_queries(
    job: PublishJob,
    publisher_cfg: dict[str, Any],
    android_cfg: dict[str, Any],
) -> list[str]:
    listing = job.listing
    mp = _mp_cfg(publisher_cfg, android_cfg)
    fallback = (mp.get("address_fallback") or "Amphoe Thalang").strip()
    return [
        q
        for q in (
            (listing.address or "").strip() or None,
            "Thalang Phuket",
            mp.get("address_search"),
            f"{listing.district} Phuket" if listing.district else None,
            "Amphoe Thalang Phuket",
            fallback,
            "Phuket Thalang",
        )
        if q
    ]


def _fill_address_picker(
    d,
    android_cfg: dict[str, Any],
    search_queries: list[str],
) -> str:
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
        d.click((x1 + x2) // 2, (y1 + y2) // 2)
        time.sleep(1.0)
        if _confirm_left_picker():
            chosen = label
            break

    if not chosen or not _confirm_left_picker():
        raise RuntimeError(
            "Не удалось выбрать адрес на Пхукете (подсказка Button, не поле ввода). "
            f"Пробовали: {', '.join(search_queries[:4])}."
        )
    return chosen


def _read_composer_location(d) -> str:
    for node in ET.fromstring(d.dump_hierarchy() or "").iter("node"):
        desc = (node.attrib.get("content-desc") or "")
        if "Местоположение" in desc and "Задать значение" in desc:
            continue
        text = (node.attrib.get("text") or "").strip()
        if text and not text.startswith("Задать значение"):
            return text
    return ""


def _location_suggestions_from_xml(xml: str) -> list[dict[str, Any]]:
    skip = {
        "Закрыть",
        "Назад",
        "Домой",
        "Поиск",
        "Search",
        "Выбор адреса",
        "Последние",
        "Применить",
        "Очистить текст",
        "Информация с карты",
        "Обновить свое местоположение",
    }
    skip_parts = ("mapbox", "очистить", "применить", "обновить свое", "добавить местоположение")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in ET.fromstring(xml).iter("node"):
        attrs = node.attrib
        cls = attrs.get("class") or ""
        if "EditText" in cls:
            continue
        label = (attrs.get("content-desc") or attrs.get("text") or "").strip()
        low = label.lower()
        if not label or len(label) < 8 or label in skip:
            continue
        if any(part in low for part in skip_parts):
            continue
        parsed = _parse_bounds(attrs.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        if y1 < 250 or y1 > 1320 or (x2 - x1) < 120:
            continue
        key = _tile_key(x1, y1, x2, y2)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "y1": y1,
                "cx": (x1 + x2) // 2,
                "cy": (y1 + y2) // 2,
                "label": label,
            }
        )
    out.sort(key=lambda item: item["y1"])
    return out


def _pick_first_location_suggestion(d) -> str:
    suggestions = _location_suggestions_from_xml(d.dump_hierarchy() or "")
    if not suggestions:
        return ""
    first = suggestions[0]
    d.click(first["cx"], first["cy"])
    return str(first["label"])


def _set_address(
    d,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
    job: PublishJob,
) -> str:
    """Адрес обязателен: пикер «Выбор адреса» → клик по Button-подсказке (не EditText)."""
    search_queries = _address_search_queries(job, publisher_cfg, android_cfg)

    opened = False
    for _ in range(4):
        if d(descriptionContains="Задать значение: Адрес").exists(timeout=0.6):
            d(descriptionContains="Задать значение: Адрес").click()
            opened = True
            break
        opened = (
            _tap_desc_contains(d, "Задать значение: Адрес")
            or _tap_desc_contains(d, "Адрес сдаваемого жилья")
            or _tap_desc_contains(d, "Адрес объекта недвижимости")
            or _tap_desc_contains(d, "Местоположение")
        )
        if not opened:
            for label in (
                "Адрес сдаваемого жилья",
                "Адрес объекта недвижимости",
                "Адрес объекта",
                "Местоположение",
            ):
                if d(text=label).exists(timeout=0.4):
                    d(text=label).click()
                    opened = True
                    break
        if opened:
            break
        _scroll_down(d, 1)
    if not opened:
        return ""

    human_pause(android_cfg, scale=0.6)
    if not d(text="Выбор адреса").exists(timeout=2):
        return "already_set"

    return _fill_address_picker(d, android_cfg, search_queries)


def _photo_tiles_from_xml(xml: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in ET.fromstring(xml).iter("node"):
        attrs = node.attrib
        if attrs.get("clickable") != "true":
            continue
        desc_raw = attrs.get("content-desc") or ""
        desc = desc_raw.lower()
        if "сделать" in desc or "camera" in desc or "камер" in desc:
            continue
        if (
            "фото" not in desc
            and "photo" not in desc
            and "дата и время" not in desc
            and "photo taken on" not in desc
        ):
            continue
        parsed = _parse_bounds(attrs.get("bounds", ""))
        if not parsed:
            continue
        x1, y1, x2, y2 = parsed
        if y1 < _PHOTO_TILE_MIN_Y or (x2 - x1) < 80:
            continue
        state = gallery_selection_state(desc_raw)
        selected = state == "selected" or (
            state is None
            and (
                attrs.get("selected") == "true"
                or attrs.get("checked") == "true"
            )
        )
        out.append(
            {
                "key": _tile_key(x1, y1, x2, y2),
                "cx": (x1 + x2) // 2,
                "cy": (y1 + y2) // 2,
                "x1": x1,
                "y1": y1,
                "selected": selected,
            }
        )
    out.sort(key=lambda item: (item["y1"], item["x1"]))
    return _unique_photo_tiles(out)


def _unique_photo_tiles(tiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for tile in tiles:
        if tile["key"] in seen:
            continue
        seen.add(tile["key"])
        unique.append(tile)
    return unique


def _gallery_showing_album(d, album: str) -> bool:
    xml = (d.dump_hierarchy() or "").lower()
    album_l = album.lower()
    if f"папка {album_l}" in xml:
        return True
    for node in ET.fromstring(d.dump_hierarchy() or "<hierarchy/>").iter("node"):
        desc = (node.attrib.get("content-desc") or "").lower()
        if "выбор альбома" in desc and album_l in desc:
            return True
    return False


def _open_gallery_album(d, album: str) -> None:
    for _ in range(3):
        if _gallery_showing_album(d, album) and _photo_tiles_from_hierarchy(d):
            return
        for sel in ("Выбор альбома", "Select album"):
            if d(descriptionContains=sel).exists(timeout=0.8):
                d(descriptionContains=sel).click()
                time.sleep(0.9)
                break
        for candidate in (f"Папка {album}", album):
            if d(descriptionContains=candidate).exists(timeout=1.5):
                d(descriptionContains=candidate).click()
                time.sleep(0.9)
                break
        else:
            if d(text=album).exists(timeout=0.5):
                d(text=album).click()
                time.sleep(0.9)
            elif _tap_desc_contains(d, album):
                time.sleep(0.9)
        if d(text="Telegram").exists(timeout=0.4) or d(
            descriptionContains="Telegram"
        ).exists(timeout=0.3):
            d.press("back")
            time.sleep(0.8)
    if not _photo_tiles_from_hierarchy(d):
        raise RuntimeError(f"Не удалось открыть альбом «{album}»")


def _photo_tiles_from_hierarchy(d) -> list[dict[str, Any]]:
    return _photo_tiles_from_xml(d.dump_hierarchy() or "")


def _tap_add_photos_button(d) -> None:
    labels = ("Добавить фото", "Add photos", "Add photo")
    for attempt in range(2):
        _hide_keyboard(d)
        for label in labels:
            if d(text=label).exists(timeout=0.5):
                d(text=label).click()
                return
            if d(description=label).exists(timeout=0.3):
                d(description=label).click()
                return
        if d(descriptionContains="Добавить фото").exists(timeout=0.3):
            d(descriptionContains="Добавить фото").click()
            return
        if attempt == 0:
            _scroll_to_top_if_needed(d, max_swipes=1)
    raise RuntimeError("«Добавить фото» не найдено")


def _add_photos(
    d,
    android_cfg: dict[str, Any],
    *,
    album: str,
    max_images: int,
    expected_count: int | None = None,
) -> tuple[int, int]:
    target = max(
        1,
        min(
            int(expected_count or max_images),
            max_images,
            _VISIBLE_MARKETPLACE_PHOTOS,
        ),
    )
    max_images = max(
        1,
        min(int(max_images), _VISIBLE_MARKETPLACE_PHOTOS),
    )
    target = min(target, max_images)

    _tap_add_photos_button(d)
    time.sleep(1.2)
    dismiss_permissions(d)

    if not _gallery_showing_album(d, album):
        _open_gallery_album(d, album)
    elif len(_photo_tiles_from_hierarchy(d)) < 2:
        _open_gallery_album(d, album)

    xml = d.dump_hierarchy() or ""
    tiles = _photo_tiles_from_xml(xml)
    if not tiles:
        raise RuntimeError(
            f"В альбоме «{album}» нет фото на экране. Сначала: prepare --push-media"
        )

    to_click = [tile for tile in tiles if not tile["selected"]][:target]
    for tile in to_click:
        d.click(tile["cx"], tile["cy"])
        time.sleep(0.35)

    selected = count_selected_gallery_photos(d.dump_hierarchy() or "")
    if selected < len(to_click):
        selected = len(to_click)

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
    return min(selected, target), target


def _composer_add_photos_visible(d) -> bool:
    for label in ("Добавить фото", "Add photos", "Add photo"):
        if d(text=label).exists(timeout=0.15) or d(description=label).exists(timeout=0.1):
            return True
    return bool(d(descriptionContains="Добавить фото").exists(timeout=0.15))


def _composer_header_visible(d) -> bool:
    if _composer_add_photos_visible(d):
        return True
    if d(resourceId="marketplace_composer_price_input").exists(timeout=0.2):
        try:
            bounds = d(resourceId="marketplace_composer_price_input").info.get("bounds") or {}
            if int(bounds.get("top", 999)) < 520:
                return True
        except Exception:
            return True
    return False


def _scroll_to_top_if_needed(d, *, max_swipes: int = 1) -> None:
    if _composer_header_visible(d):
        return
    for _ in range(max_swipes):
        d.swipe(0.5, 0.28, 0.5, 0.82, 0.2)
        time.sleep(0.2)
        if _composer_header_visible(d):
            return


def _scroll_to_top(d, n: int = 1) -> None:
    if n <= 0:
        return
    if n == 1:
        _scroll_to_top_if_needed(d, max_swipes=1)
        return
    for _ in range(n):
        d.swipe(0.5, 0.28, 0.5, 0.82, 0.2)
        time.sleep(0.2)


def _scroll_down(d, n: int = 1) -> None:
    for _ in range(n):
        d.swipe(0.5, 0.75, 0.5, 0.35, 0.25)
        time.sleep(0.25)


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
    expected_photos: int | None = None,
    session: MarketplaceSession | None = None,
) -> dict[str, Any]:
    cfg = _mp_cfg(publisher_cfg, android_cfg)
    album = album or cfg.get("album") or ALBUM_DEFAULT
    max_images = int(
        max_images
        or cfg.get("max_images")
        or (publisher_cfg.get("media") or {}).get("max_marketplace_images")
        or _MAX_MARKETPLACE_PHOTOS
    )
    listing = job.listing
    meta: dict[str, Any] = {}

    _scroll_to_top_if_needed(d)
    _assert_form_not_limited(d)
    if _is_composer_form(d):
        return _fill_composer_form(
            d,
            job,
            android_cfg,
            publisher_cfg,
            album=album,
            max_images=max_images,
            expected_photos=expected_photos,
            session=session,
        )

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

    caption = (job.caption_fb or "").strip()
    if caption:
        if d(text="Выбор адреса").exists(timeout=0.3):
            raise RuntimeError("Остались на «Выбор адреса» — адрес не подтверждён")
        _set_listing_description(d, caption, android_cfg)
        meta["caption_len"] = len(caption)

    _scroll_to_top_if_needed(d)
    if d(text="Выбор адреса").exists(timeout=0.3):
        raise RuntimeError("Перед фото снова экран «Выбор адреса»")
    photo_target = min(
        int(expected_photos or max_images),
        max_images,
        _VISIBLE_MARKETPLACE_PHOTOS,
    )
    selected, expected = _add_photos(
        d,
        android_cfg,
        album=album,
        max_images=max_images,
        expected_count=photo_target,
    )
    meta["photos"] = selected
    meta["photos_expected"] = expected
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
    session: MarketplaceSession | None = None,
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

    if session is not None:
        session.refresh_snapshot()
        session.confirm_publish_allowed()

    if d(description="Опубликовать").exists(timeout=2):
        d(description="Опубликовать").click()
    elif d(text="Опубликовать").exists(timeout=1):
        d(text="Опубликовать").click()
    else:
        return ChannelResult(
            channel=channel,
            ok=False,
            reason="publish_button_not_found",
            publication_status="failed",
        )
    human_pause(android_cfg, scale=1.5)
    publication_status = "accepted"
    note = "Нажато «Опубликовать» — дождитесь загрузки"
    if session is not None:
        verified, verify_reason = session.verify_after_publish()
        if not verified:
            return ChannelResult(
                channel=channel,
                ok=False,
                reason=f"publish_not_verified:{verify_reason}",
                note=note,
                publication_status="needs_review",
            )
        publication_status = "submitted_unverified"
    return ChannelResult(
        channel=channel,
        ok=True,
        note=note,
        publication_status=publication_status,
    )


def resume_composer_from_location(
    d,
    job: PublishJob,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
    *,
    confirm_post: bool = False,
) -> tuple[dict[str, Any], ChannelResult]:
    """Продолжить composer-форму с шага «Местоположение» (фото/название/цена уже на экране)."""
    if not _is_composer_form(d) and not _on_location_map_screen(d):
        raise RuntimeError(
            "Откройте форму «Новое объявление» или экран карты местоположения"
        )
    listing = job.listing
    meta: dict[str, Any] = {"form": "composer", "resume": "location"}

    addr = _set_composer_location(d, android_cfg, publisher_cfg, job)
    if not addr:
        raise RuntimeError("Не удалось указать местоположение")
    meta["address"] = addr

    price = _format_price(listing.price_monthly)
    caption = (job.caption_fb or "").strip()
    if caption:
        if price and _composer_price_value(d) != price:
            _set_composer_price(d, price)
        _set_composer_description(d, caption, android_cfg)
        meta["caption_len"] = len(caption)

    _go_to_publish_screen(d, android_cfg)
    result = _publish_or_stop(
        d,
        confirm_post=confirm_post,
        android_cfg=android_cfg,
    )
    return meta, result


def resume_composer_from_description(
    d,
    job: PublishJob,
    android_cfg: dict[str, Any],
    publisher_cfg: dict[str, Any],
    *,
    confirm_post: bool = False,
) -> tuple[dict[str, Any], ChannelResult]:
    """Продолжить composer-форму: только описание (+ публикация при --live)."""
    if not _is_composer_form(d):
        raise RuntimeError("Откройте форму «Новое объявление» на телефоне")
    meta: dict[str, Any] = {"form": "composer", "resume": "description"}
    caption = (job.caption_fb or "").strip()
    if not caption:
        raise RuntimeError("caption_fb пустой в Notion")
    _set_composer_description(d, caption, android_cfg)
    meta["caption_len"] = len(caption)
    _go_to_publish_screen(d, android_cfg)
    result = _publish_or_stop(
        d,
        confirm_post=confirm_post,
        android_cfg=android_cfg,
    )
    return meta, result


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
        album = marketplace_folder_name(job.object_id)
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

            session = MarketplaceSession.create(d, job, publisher_cfg)
            _ensure_personal_profile(d, android_cfg)
            _open_listing_form(d, android_cfg)
            session.refresh_snapshot()
            meta = _fill_form(
                d,
                job,
                android_cfg,
                publisher_cfg,
                album=album,
                max_images=min(
                    len(images),
                    _VISIBLE_MARKETPLACE_PHOTOS,
                    int(mp_cfg.get("max_images") or _VISIBLE_MARKETPLACE_PHOTOS),
                ),
                expected_photos=min(len(images), _VISIBLE_MARKETPLACE_PHOTOS),
                session=session,
            )
            note = f"{note}; filled={meta}"
            _go_to_publish_screen(d, android_cfg)
            session.refresh_snapshot()
            result = _publish_or_stop(
                d,
                confirm_post=confirm_post,
                android_cfg=android_cfg,
                session=session,
            )
            # ссылку на объявление Marketplace не копируем — не нужна
            result.note = f"{(result.note or '')}; {note}".strip("; ")
            return result
        except MarketplaceSafeStop as stop:
            return ChannelResult(
                channel=self.name,
                ok=False,
                reason=stop.reason,
                note=note,
                publication_status=stop.publication_status,
            )
        except Exception as e:
            if "session" in locals():
                try:
                    session.save_diagnostics(reason=str(e), exc=e)
                except Exception:
                    pass
            return ChannelResult(
                channel=self.name,
                ok=False,
                reason=str(e),
                note=note,
            )
