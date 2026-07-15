#!/usr/bin/env python3
"""Agent 4 — ветка 3: объявление объекта из Notion CRM в Facebook Marketplace.

Заполняет форму «Homes for Rent» (marketplace/create/rental) браузерной
автоматизацией — Playwright, тот же персистентный профиль FB, что у ветки
групп (fb_groups_pipeline.py) и парсера. Отдельный процесс, отдельные
lock-поля (fb_marketplace_locked / fb_marketplace_taken_at).

Данные из строки объекта: описание — «Описание для FB Marketplace» как есть
(тег #A_YYYYMMDD_NNN в конце), цена — «Цена за месяц», спальни/санузлы/тип
жилья/район — соответствующие колонки, фото — галерея R2 из «Фото»
(хук-обложка первой). Результат — ссылка в post_url_FB_marketplace.

Запуск (python3.11 с playwright — venv парсера):
  VENV=agent_1_parser/fb_parser/.venv311/bin/python
  $VENV fb_marketplace_pipeline.py --page-id PAGE_ID [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import publish_pipeline as pp
import fb_groups_pipeline as fgg  # браузер, профиль, задержки, фото, состояние лимитов
import fb_account_guard as guard  # общий предохранитель FB-аккаунта

# Метки полей формы (EN/UK/RU — язык интерфейса аккаунта может меняться)
LABEL_RENT_OR_SALE = re.compile(r"продаж або оренда|home for sale or rent|продажа или аренда", re.I)
LABEL_PROPERTY_TYPE = re.compile(
    r"property type|rental type|тип нерухомості|тип оренди|тип недвижимости|тип аренды", re.I
)
LABEL_BEDROOMS = re.compile(r"number of bedrooms|кількість спалень|количество спален", re.I)
LABEL_BATHROOMS = re.compile(r"number of bathrooms|кількість ванних|количество ванных|сануз", re.I)
LABEL_PRICE = re.compile(r"^(ціна|price|цена)", re.I)
LABEL_DESCRIPTION = re.compile(
    r"property description|rental description|опис (нерухомості|житла|оренди)|описание", re.I
)
LABEL_SQM = re.compile(r"квадратні метри|square meters|квадратные метры", re.I)
LABEL_ADDRESS = re.compile(r"адреса|address|адрес|location|розташування|местоположение", re.I)

OPTION_RENT = re.compile(r"^(rent|в оренду|оренда|аренда|в аренду)$", re.I)
PROPERTY_TYPE_OPTIONS = {
    "house": re.compile(r"будинок|house|дом", re.I),
    "townhouse": re.compile(r"таунхаус|townhouse", re.I),
    "apartment": re.compile(r"квартира|апартаменти|apartment|condo|кондо", re.I),
    # в форме всего 3 типа: квартира/будинок/таунхаус; room маппим на квартиру
    "room": re.compile(r"квартира|apartment", re.I),
}

NEXT_BUTTON_RE = re.compile(r"^(далі|next|далее)$", re.I)
PUBLISH_BUTTON_RE = re.compile(r"^(опублікувати|publish|опубликовать)$", re.I)
ITEM_HREF_RE = re.compile(r"/marketplace/item/(\d+)")


def load_mp_config() -> dict[str, Any]:
    config_path = pp.package_root() / "config" / "fb_marketplace.json"
    with config_path.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Локация: координаты из колонки «Google Maps» -> название места (reverse geocode)
# ---------------------------------------------------------------------------

def coords_from_maps_url(url: str | None) -> tuple[float, float] | None:
    """https://www.google.com/maps?q=8.002100,98.307900 -> (8.0021, 98.3079)."""
    if not url:
        return None
    m = re.search(r"[?&]q=(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", url)
    if not m:
        m = re.search(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)", url)
    if not m:
        return None
    lat, lng = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def reverse_geocode(lat: float, lng: float, cfg: dict[str, Any]) -> str | None:
    """Координаты -> «Choeng Thale, Thalang District, Phuket» (Google Geocoding API)."""
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return None
    language = cfg.get("listing", {}).get("reverse_geocode_language", "en")
    params = urllib.parse.urlencode(
        {
            "latlng": f"{lat},{lng}",
            "key": api_key,
            "language": language,
            "result_type": "sublocality|locality|administrative_area_level_2",
        }
    )
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    results = data.get("results") or []
    if not results:
        return None

    wanted = [
        ("sublocality", "sublocality_level_1", "locality"),
        ("administrative_area_level_2",),
        ("administrative_area_level_1",),
    ]
    parts: list[str] = []
    components = results[0].get("address_components", [])
    for group in wanted:
        for comp in components:
            if any(t in comp.get("types", []) for t in group):
                name = comp.get("long_name", "").strip()
                if name and name not in parts:
                    parts.append(name)
                break
    if parts:
        return ", ".join(parts)
    formatted = results[0].get("formatted_address", "").strip()
    return formatted or None


def location_queries(page: dict[str, Any], fields: dict[str, str], cfg: dict[str, Any]) -> list[str]:
    """Кандидаты для typeahead локации, в порядке приоритета."""
    listing_cfg = cfg.get("listing", {})
    queries: list[str] = []

    maps_url = pp.get_prop(page, fields.get("google_maps", "Google Maps"), "url")
    coords = coords_from_maps_url(maps_url)
    if coords:
        place = reverse_geocode(*coords, cfg)
        if place:
            queries.append(place)
            # укороченный вариант («Choeng Thale») — если полный не найдётся
            first = place.split(",")[0].strip()
            if first and first != place:
                queries.append(first)

    suffix = listing_cfg.get("location_suffix", "")
    for key in ("district", "address"):
        value = pp.get_prop(page, fields[key], "rich_text") if fields.get(key) else None
        if value:
            q = value.strip()
            if suffix and suffix.lower() not in q.lower():
                q = f"{q}, {suffix}"
            queries.append(q)

    seen: set[str] = set()
    return [q for q in queries if not (q in seen or seen.add(q))]


def debug_screenshot(page: Any, name: str) -> str:
    out_dir = pp.package_root() / "data" / "fb_marketplace" / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}_{datetime.now(timezone.utc).strftime('%H%M%S')}.png"
    try:
        page.screenshot(path=str(path))
    except Exception:
        return ""
    return str(path)


# ---------------------------------------------------------------------------
# Заполнение формы
# ---------------------------------------------------------------------------

def select_combobox(page: Any, label_re: re.Pattern, option_re: re.Pattern, what: str) -> None:
    combo = page.locator('label[role="combobox"]', has_text=label_re)
    if not combo.count():
        raise RuntimeError(
            f"MP_FIELD_NOT_FOUND: не нашёл комбобокс «{what}». "
            "Скриншот: " + debug_screenshot(page, f"no_combo_{what}")
        )
    combo.first.click()
    option = page.locator('[role="listbox"] [role="option"], [role="menu"] [role="menuitem"]').filter(
        has_text=option_re
    )
    option.first.wait_for(state="visible", timeout=15000)
    option.first.click()


def fill_labeled_input(page: Any, label_re: re.Pattern, value: str, what: str, *, textarea: bool = False) -> None:
    label = page.locator("label", has_text=label_re)
    if not label.count():
        raise RuntimeError(
            f"MP_FIELD_NOT_FOUND: не нашёл поле «{what}». "
            "Скриншот: " + debug_screenshot(page, f"no_field_{what}")
        )
    selector = "textarea" if textarea else "input"
    target = label.first.locator(selector)
    if not target.count():
        # input может быть соседом label внутри общего контейнера
        target = label.first.locator("xpath=..").locator(selector)
    if not target.count():
        raise RuntimeError(f"MP_FIELD_NOT_FOUND: у метки «{what}» нет {selector}")
    target.first.click()
    target.first.fill(str(value))


def _find_location_input(page: Any) -> Any | None:
    """Поле локации — единственный text-input без текстовой метки (только пин-иконка)."""
    label = page.locator("label", has_text=LABEL_ADDRESS)
    if label.count():
        return label.first.locator("input").first
    candidates = page.locator('label:has(input[type="text"])')
    for i in range(candidates.count()):
        lab = candidates.nth(i)
        try:
            if not (lab.inner_text(timeout=800) or "").strip():
                return lab.locator("input").first
        except Exception:
            continue
    return None


def fill_location(page: Any, queries: list[str], cfg: dict[str, Any]) -> str:
    """Локация — typeahead: пробуем кандидатов по порядку, выбираем первый вариант."""
    inp = _find_location_input(page)
    if inp is None:
        raise RuntimeError(
            "MP_FIELD_NOT_FOUND: не нашёл поле адреса/локации. "
            "Скриншот: " + debug_screenshot(page, "no_location")
        )
    option = page.locator('[role="listbox"] [role="option"]')
    for query in queries:
        inp.click()
        inp.fill(query)
        try:
            option.first.wait_for(state="visible", timeout=10000)
            fgg.human_delay(cfg)
            option.first.click()
            return query
        except Exception:
            continue
    raise RuntimeError(
        f"MP_LOCATION_NOT_FOUND: typeahead не предложил вариантов ни для одного из: "
        f"{queries}. Скриншот: " + debug_screenshot(page, "location_no_options")
    )


PREVIEW_IMG = 'img[src^="blob:"], img[src*="scontent"]'


def attach_photos(page: Any, files: list[Path], cfg: dict[str, Any]) -> None:
    file_input = page.locator('input[type="file"][accept*="image"]')
    if not file_input.count():
        file_input = page.locator('input[type="file"]')
    if not file_input.count():
        raise RuntimeError("MP_PHOTO_INPUT_NOT_FOUND: нет input для фото")

    # Форма Marketplace сразу заливает фото на CDN: превью получают scontent-URL.
    # Ждём прирост числа превью относительно уровня до загрузки.
    baseline = page.locator(PREVIEW_IMG).count()
    file_input.first.set_input_files([str(f) for f in files])

    deadline = time.time() + int(cfg.get("browser", {}).get("upload_wait_ms", 120000)) / 1000.0
    want = len(files)
    last_count, stable_since, added = -1, None, 0
    while time.time() < deadline:
        added = page.locator(PREVIEW_IMG).count() - baseline
        if added >= want:
            return
        if added > 0:
            if added != last_count:
                stable_since = time.time()
            elif stable_since and time.time() - stable_since >= 10.0:
                # часть фото могла не пройти — едем дальше с тем, что загрузилось
                return
        last_count = added
        time.sleep(1.0)
    raise RuntimeError(f"MP_UPLOAD_TIMEOUT: загрузилось {added} из {want} превью")


def click_button(page: Any, name_re: re.Pattern, what: str, *, timeout_s: float = 60.0) -> None:
    btn = page.get_by_role("button", name=name_re)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if btn.count():
            b = btn.first
            if b.get_attribute("aria-disabled") not in ("true", "1"):
                b.scroll_into_view_if_needed()
                b.click()
                return
        time.sleep(1.0)
    raise RuntimeError(
        f"MP_BUTTON_DISABLED: кнопка «{what}» не активировалась. "
        "Скриншот: " + debug_screenshot(page, f"button_{what}")
    )


def collect_item_ids(page: Any) -> set[str]:
    ids: set[str] = set()
    for a in page.locator('a[href*="/marketplace/item/"]').all():
        m = ITEM_HREF_RE.search(a.get_attribute("href") or "")
        if m:
            ids.add(m.group(1))
    return ids


def find_new_listing_url(
    page: Any, cfg: dict[str, Any], before: set[str], listing: dict[str, Any]
) -> str | None:
    """После публикации ищем ссылку нового объявления в «Ваши объявления».

    Карточки в /you/selling не содержат прямого href на item — ссылка появляется
    в боковой панели после клика по карточке.
    """
    wait_ms = int(cfg.get("browser", {}).get("post_confirm_wait_ms", 60000))
    deadline = time.time() + wait_ms / 1000.0
    selling_url = cfg.get("selling_url", "https://www.facebook.com/marketplace/you/selling")
    card_title = f"{listing['bedrooms']} Beds {listing['bathrooms']} Baths"

    while time.time() < deadline:
        # публикация могла средиректить прямо на объявление
        m = ITEM_HREF_RE.search(page.url)
        if m and m.group(1) not in before:
            return f"https://www.facebook.com/marketplace/item/{m.group(1)}/"
        new_ids = collect_item_ids(page) - before
        if new_ids:
            newest = sorted(new_ids, reverse=True)[0]
            return f"https://www.facebook.com/marketplace/item/{newest}/"

        try:
            page.goto(selling_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(5.0)
            new_ids = collect_item_ids(page) - before
            if new_ids:
                newest = sorted(new_ids, reverse=True)[0]
                return f"https://www.facebook.com/marketplace/item/{newest}/"
            # клик по свежей карточке открывает панель с прямой ссылкой на item
            card = page.locator('[role="main"]').get_by_text(card_title, exact=False)
            if card.count():
                card.first.click()
                time.sleep(5.0)
                new_ids = collect_item_ids(page) - before
                if new_ids:
                    newest = sorted(new_ids, reverse=True)[0]
                    return f"https://www.facebook.com/marketplace/item/{newest}/"
        except Exception:
            pass
        time.sleep(3.0)
    return None


def create_listing(page: Any, listing: dict[str, Any], files: list[Path], cfg: dict[str, Any]) -> dict[str, Any]:
    browser_cfg = cfg.get("browser", {})
    nav_timeout = int(browser_cfg.get("nav_timeout_ms", 90000))

    # Снимок «моих объявлений» до публикации — чтобы вычислить новое
    page.goto(
        cfg.get("selling_url", "https://www.facebook.com/marketplace/you/selling"),
        wait_until="domcontentloaded",
        timeout=nav_timeout,
    )
    fgg.human_delay(cfg, 2.0)
    before = collect_item_ids(page)

    page.goto(cfg["create_url"], wait_until="domcontentloaded", timeout=nav_timeout)
    fgg.human_delay(cfg, 3.0)
    if "/marketplace/create" not in page.url:
        raise RuntimeError(
            f"MP_FORM_UNAVAILABLE: редирект на {page.url} — возможно, Marketplace "
            "недоступен аккаунту. Скриншот: " + debug_screenshot(page, "form_unavailable")
        )

    attach_photos(page, files, cfg)
    fgg.human_delay(cfg)

    select_combobox(page, LABEL_RENT_OR_SALE, OPTION_RENT, "аренда/продажа")
    fgg.human_delay(cfg)

    ptype = listing["property_type"]
    select_combobox(page, LABEL_PROPERTY_TYPE, PROPERTY_TYPE_OPTIONS[ptype], f"тип ({ptype})")
    fgg.human_delay(cfg)

    fill_labeled_input(page, LABEL_BEDROOMS, listing["bedrooms"], "спальни")
    fgg.human_delay(cfg)
    fill_labeled_input(page, LABEL_BATHROOMS, listing["bathrooms"], "санузлы")
    fgg.human_delay(cfg)
    fill_labeled_input(page, LABEL_PRICE, listing["price"], "цена")
    fgg.human_delay(cfg)
    used_query = fill_location(page, listing["location_queries"], cfg)
    listing["location_used"] = used_query
    fgg.human_delay(cfg, 2.0)

    # Описание и кв.метры могут быть ниже на этом же шаге или на следующем
    # (форма многошаговая, шаги переключает «Далі»).
    for _ in range(4):
        desc_label = page.locator("label", has_text=LABEL_DESCRIPTION)
        if desc_label.count():
            break
        click_button(page, NEXT_BUTTON_RE, "далее", timeout_s=30.0)
        fgg.human_delay(cfg, 2.0)
    fill_labeled_input(page, LABEL_DESCRIPTION, listing["description"], "описание", textarea=True)
    fgg.human_delay(cfg)
    if listing.get("area_sqm"):
        try:
            fill_labeled_input(page, LABEL_SQM, listing["area_sqm"], "площадь")
        except RuntimeError:
            pass  # поле опциональное — есть не во всех вариантах формы

    debug_screenshot(page, "form_filled")

    # До «Опублікувати» может остаться ещё шаг-другой («Далі»)
    for _ in range(4):
        if page.get_by_role("button", name=PUBLISH_BUTTON_RE).count():
            break
        click_button(page, NEXT_BUTTON_RE, "далее", timeout_s=30.0)
        fgg.human_delay(cfg, 2.0)
    click_button(page, PUBLISH_BUTTON_RE, "опубликовать", timeout_s=90.0)
    fgg.human_delay(cfg, 3.0)

    url = find_new_listing_url(page, cfg, before, listing)
    return {"post_url": url}


# ---------------------------------------------------------------------------
# Пайплайн
# ---------------------------------------------------------------------------

def build_listing(page: dict[str, Any], fields: dict[str, str], cfg: dict[str, Any]) -> dict[str, Any]:
    listing_cfg = cfg.get("listing", {})

    caption = pp.get_prop(page, fields["caption"], "rich_text") or ""
    price = pp.get_prop(page, fields["price_monthly"], "number")
    bedrooms = pp.get_prop(page, fields["rooms"], "number")
    bathrooms = pp.get_prop(page, fields["bathrooms"], "number")
    housing = pp.get_prop(page, fields["housing_type"], "select") or ""
    area = pp.get_prop(page, fields["area_sqm"], "number")

    missing = [
        name
        for name, val in [
            (fields["caption"], caption.strip()),
            (fields["price_monthly"], price),
            (fields["rooms"], bedrooms),
            (fields["bathrooms"], bathrooms),
        ]
        if not val
    ]
    if missing:
        raise ValueError(f"Не заполнены обязательные колонки: {', '.join(missing)}")

    type_map = listing_cfg.get("property_type_by_housing", {})
    property_type = type_map.get(housing) or type_map.get("default", "house")

    queries = location_queries(page, fields, cfg)
    if not queries:
        raise ValueError(
            "Не из чего собрать локацию: пусты «Google Maps», «Район» и «Адрес»"
        )

    return {
        "description": caption,
        "price": int(price),
        "bedrooms": int(bedrooms),
        "bathrooms": int(bathrooms),
        "property_type": property_type,
        "housing_type": housing,
        "area_sqm": int(area) if area else None,
        "location_queries": queries,
    }


def check_rate_limits(cfg: dict[str, Any]) -> str | None:
    limits = cfg.get("limits", {})
    now = datetime.now(timezone.utc)
    posts = fgg.load_state(cfg).get("posts", [])

    from datetime import timedelta

    day_ago = now - timedelta(days=1)
    last_day = [p for p in posts if datetime.fromisoformat(p["ts"]) > day_ago]
    max_day = int(limits.get("max_listings_per_day", 3))
    if len(last_day) >= max_day:
        return f"дневной лимит {max_day} объявлений исчерпан"

    gap = int(limits.get("min_minutes_between_listings", 120))
    recent = [p for p in posts if datetime.fromisoformat(p["ts"]) > now - timedelta(minutes=gap)]
    if recent:
        return f"минимальный интервал {gap} мин не выдержан (последнее: {recent[-1]['ts']})"
    return None


def publish_marketplace(page_id: str, cfg: dict[str, Any], *, dry_run: bool, force: bool) -> dict[str, Any]:
    fields = cfg["notion"]["fields"]
    status_ready = cfg["notion"]["statuses"]["ready"]

    page = pp.notion_get_page(page_id)
    object_id = pp.get_prop(page, fields["object_id"], "rich_text") or ""
    status = pp.get_prop(page, fields["status"], "status")
    gallery_url = pp.get_prop(page, fields["photo"], "url") or ""
    locked = bool(pp.get_prop(page, fields["fb_marketplace_locked"], "checkbox"))
    existing_url = pp.get_prop(page, fields["post_url_fb_marketplace"], "url")

    result: dict[str, Any] = {"page_id": page_id, "object_id": object_id, "status": status}

    if locked and not force:
        return {**result, "skipped": True, "reason": "fb_marketplace_locked"}
    if existing_url and not force:
        return {**result, "skipped": True, "reason": f"уже опубликовано: {existing_url}"}
    if status != status_ready and not force:
        return {**result, "skipped": True, "reason": f"status={status}, ожидался {status_ready}"}
    if not gallery_url:
        raise ValueError(f"Пустая колонка «{fields['photo']}» у {object_id or page_id}")

    listing = build_listing(page, fields, cfg)
    result["listing"] = {**listing, "description": listing["description"][:120]}

    image_urls = fgg.resolve_image_urls(gallery_url, cfg)
    if not image_urls:
        raise ValueError(f"В галерее {gallery_url} не найдено изображений")
    result["images"] = len(image_urls)

    if not force:
        guard_reason = guard.check_account_guard()
        if guard_reason:
            return {**result, "skipped": True, "reason": f"предохранитель: {guard_reason}"}
        reason = check_rate_limits(cfg)
        if reason:
            return {**result, "skipped": True, "reason": f"лимит: {reason}"}

    if dry_run:
        return {**result, "dry_run": True}

    files = fgg.download_images(image_urls, object_id or page_id, cfg)

    from playwright.sync_api import sync_playwright

    pp.notion_update_fields(
        page_id,
        {
            fields["fb_marketplace_locked"]: pp.notion_checkbox_property(True),
            fields["fb_marketplace_taken_at"]: pp.notion_date_property(pp.utc_today_iso()),
        },
    )

    submitted = False
    lock_file = None
    out: dict[str, Any] = {}
    try:
        profile = fgg.profile_path(cfg)
        if not profile.exists():
            raise RuntimeError(f"Профиль браузера не найден: {profile}")
        lock_file = fgg.acquire_profile_lock(profile)

        with sync_playwright() as p:
            context = fgg.open_browser(p, cfg)
            try:
                bpage = context.pages[0] if context.pages else context.new_page()
                bpage.goto(
                    "https://www.facebook.com/",
                    wait_until="domcontentloaded",
                    timeout=int(cfg.get("browser", {}).get("nav_timeout_ms", 90000)),
                )
                fgg.human_delay(cfg)
                if not fgg.is_logged_in(context):
                    fgg.try_relogin(bpage, cfg)
                    if not fgg.is_logged_in(context):
                        raise RuntimeError("AUTH_REQUIRED: логин не удался")

                # «Пришёл человек»: полистать ленту до и после публикации
                fgg.idle_scroll(bpage, cfg, (15, 40))
                out = create_listing(bpage, listing, files, cfg)
                submitted = True
                fgg.record_post(cfg, "marketplace", object_id, out.get("post_url") or "")
                fgg.idle_scroll(bpage, cfg, (10, 25))
            finally:
                context.close()
                guard.record_session_end(branch="fb_marketplace")
    except Exception as e:
        if not submitted:
            pp.notion_update_fields(
                page_id, {fields["fb_marketplace_locked"]: pp.notion_checkbox_property(False)}
            )
        fgg.mark_failure(page_id, page, fields, f"fb_marketplace: {e}")
        raise
    finally:
        if lock_file:
            lock_file.unlink(missing_ok=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    post_url = out.get("post_url")
    log_field = fields["fb_marketplace_log"]
    old_log = pp.get_prop(page, log_field, "rich_text") or ""
    line = f"{now} marketplace -> {post_url or 'опубликовано, url не найден'}"
    combined = (old_log + "\n" + line).strip()[-1900:]

    props: dict[str, Any] = {
        fields["publish_error"]: {"rich_text": []},
        log_field: {"rich_text": [{"text": {"content": combined}}]},
    }
    if post_url:
        props[fields["post_url_fb_marketplace"]] = pp.notion_url_property(post_url)
    pp.notion_update_fields(page_id, props)

    result["posted"] = out
    result["saved_url"] = post_url
    return result


def main() -> int:
    pp.load_dotenv()
    cfg = load_mp_config()

    parser = argparse.ArgumentParser(description="Notion CRM → Facebook Marketplace (Playwright)")
    parser.add_argument("--page-id", required=True, help="Notion page ID объекта")
    parser.add_argument("--dry-run", action="store_true", help="Проверка без постинга")
    parser.add_argument("--force", action="store_true", help="Игнорировать lock и лимиты")
    parser.add_argument("--skip-schema-check", action="store_true", help="Пропустить проверку схемы")
    args = parser.parse_args()

    pp.run_schema_check(args.skip_schema_check)

    try:
        out = publish_marketplace(args.page_id, cfg, dry_run=args.dry_run, force=args.force)
        print(json.dumps(out, indent=2, ensure_ascii=False))
    except fgg.ProfileBusy as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
