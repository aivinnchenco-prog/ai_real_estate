#!/usr/bin/env python3
"""
Agent 2 — структуризация объекта в Notion + загрузка фото в R2.
One session → one object_id (session.json).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from real_estate_handler import (  # noqa: E402
    CloudflareR2,
    NotionCRM,
    ObjectIDGenerator,
)
from description_writer import (  # noqa: E402
    build_context,
    cjk_ratio,
    generate_long_description,
    generate_social_description,
    translate_to_russian,
)
from maps_resolver import resolve_google_maps  # noqa: E402
from gallery_html import build_gallery_html  # noqa: E402
from listing_parser import parse_listing  # noqa: E402
from housing_type import detect_housing_type  # noqa: E402
from session_store import bind_object_id, get_object_id, load_session, save_session  # noqa: E402
from event_log import log_event  # noqa: E402


def load_dotenv() -> None:
    for name in (".env.real-estate", ".env"):
        p = ROOT / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def load_config() -> dict:
    with (ROOT / "config" / "pipeline.json").open(encoding="utf-8") as f:
        cfg = json.load(f)
    # Общий config/project.json монорепы (контакты, регион) приоритетнее pipeline.json
    for candidate in (ROOT.parents[2] / "config" / "project.json",):
        if candidate.exists():
            try:
                project = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                break
            cfg["contacts"] = {**cfg.get("contacts", {}), **project.get("contacts", {})}
            if project.get("region"):
                cfg["region"] = project["region"]
            break
    return cfg


def extract_source_url(source: str) -> str | None:
    m = re.search(r"https?://[^\s]+", source)
    return m.group(0).rstrip(".,)") if m else None


def load_parsed_meta(session_path: Path) -> dict:
    """parsed.json сессии от Агента 1 (source, координаты, владелец и т.д.)."""
    parsed_path = session_path / "parsed.json"
    if parsed_path.exists():
        try:
            return json.loads(parsed_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def extract_coords(meta: dict) -> tuple[float, float] | None:
    """Координаты объявления из parsed.json (карта Airbnb/FB)."""
    loc = meta.get("location") or {}
    lat = loc.get("latitude", meta.get("latitude"))
    lng = loc.get("longitude", meta.get("longitude"))
    try:
        if lat is not None and lng is not None:
            return float(lat), float(lng)
    except (TypeError, ValueError):
        pass
    return None


def extract_listing_address(meta: dict) -> str:
    """Адрес из блока «Где вы будете» Airbnb (location.subtitle в parsed.json)."""
    loc = meta.get("location") or {}
    return str(loc.get("subtitle") or "").strip()


def detect_source_prefix(session_id: str, source: str, session_path: Path) -> str:
    """F (Facebook) / A (Airbnb) — из parsed.json сессии, ID сессии или строки источника."""
    parsed_path = session_path / "parsed.json"
    if parsed_path.exists():
        try:
            src = str(json.loads(parsed_path.read_text(encoding="utf-8")).get("source", "")).upper()
            if src.startswith("A"):
                return "A"
            if src.startswith("F"):
                return "F"
        except (OSError, ValueError):
            pass
    sid = session_id.upper()
    if sid.startswith("A"):
        return "A"
    if sid.startswith("F"):
        return "F"
    low = (source or "").lower()
    if "airbnb" in low:
        return "A"
    return "F"


def resolve_object_id(
    session_id: str,
    crm: NotionCRM,
    force_new: bool = False,
    source: str = "F",
) -> str:
    """Strictly one object_id per session."""
    if not force_new:
        existing = get_object_id(session_id)
        if existing:
            return existing
    object_id = ObjectIDGenerator.generate(crm, source=source)
    bind_object_id(session_id, object_id)
    return object_id


def format_owner_line(owner: dict) -> str:
    """«Владелец / Агент»: имя хозяина, компания и найденные контакты."""
    parts = []
    if owner.get("host_name"):
        parts.append(owner["host_name"])
    if owner.get("company"):
        src = owner.get("company_source", "")
        parts.append(f"Компания: {owner['company']}" + (f" ({src})" if src else ""))
    contacts = owner.get("contacts") or {}
    if contacts.get("phones"):
        parts.append("Тел: " + ", ".join(contacts["phones"][:3]))
    if contacts.get("emails"):
        parts.append("Email: " + ", ".join(contacts["emails"][:2]))
    if contacts.get("links"):
        parts.append("Сайт: " + ", ".join(contacts["links"][:2]))
    return " | ".join(parts)[:1900]


def first_upcoming_month_entry(
    monthly: dict, today: date | None = None
) -> tuple[str | None, dict | None]:
    """Первый предстоящий месяц с ценой (строго после текущего календарного).

    Сегодня 16.07 → ищем с 2026-08 (август), не июль.
    """
    today = today or date.today()
    current = f"{today.year:04d}-{today.month:02d}"
    for month in sorted(monthly):
        if month <= current:
            continue
        entry = monthly.get(month) or {}
        if entry.get("price"):
            return month, entry
    return None, None


def apply_parsed_meta(
    properties: dict,
    parsed_meta: dict,
    nf: dict,
    *,
    today: date | None = None,
) -> None:
    """Данные Airbnb-парсера (parsed.json): владелец, календарь, цены по месяцам."""
    if not parsed_meta:
        return

    owner = parsed_meta.get("owner") or {}
    owner_line = format_owner_line(owner)
    if owner_line and nf.get("owner"):
        properties[nf["owner"]] = NotionCRM.build_text(owner_line)

    calendar_url = parsed_meta.get("calendar_url") or parsed_meta.get("source_url")
    if calendar_url and nf.get("calendar"):
        properties[nf["calendar"]] = NotionCRM.build_url(calendar_url)

    monthly = parsed_meta.get("monthly_prices") or {}
    priced_n = sum(1 for v in (monthly or {}).values() if (v or {}).get("price"))
    min_months = int(os.environ.get("PRICE_MIN_MONTHS", "3"))
    # При отложенном сборе (VPS) один сид-месяц не пишем в multi_select —
    # иначе в таблице «как будто готово», а добор на Mac откладывается.
    monthly_chips = monthly
    if parsed_meta.get("prices_deferred") and priced_n < min_months:
        monthly_chips = {}

    if monthly_chips and nf.get("monthly_prices"):
        options = monthly_price_options(monthly_chips)
        if options:
            properties[nf["monthly_prices"]] = NotionCRM.build_multi_select(options)

    # «Цена за месяц» — из полного monthly (включая сид), даже если чипы ещё не пишем
    _, entry = first_upcoming_month_entry(monthly, today=today)
    if entry and entry.get("price") and nf.get("price_monthly"):
        properties[nf["price_monthly"]] = NotionCRM.build_number(float(entry["price"]))


def monthly_price_options(monthly: dict) -> list[str]:
    """Плашки multi_select «2026-09 · 99 200 ฿»; «≈» — цена экстраполирована
    (prorated), месяцы без цены пропускаем. Этот формат парсит Qualifier."""
    options = []
    for month in sorted(monthly):
        entry = monthly.get(month) or {}
        price = entry.get("price")
        if not price:
            continue
        sep = "≈" if entry.get("status") == "prorated" else "·"
        pretty = f"{int(price):,}".replace(",", " ")
        options.append(f"{month} {sep} {pretty} ฿")
    return options


def build_notion_properties(
    *,
    draft,
    maps,
    nf: dict,
    statuses: dict,
    object_id: str,
    description: str,
    gallery_url: str | None,
    caption_tg: str,
    caption_fb: str,
    source: str,
    listing_address: str = "",
) -> dict:
    properties = {
        nf["title"]: NotionCRM.build_title(draft.title),
        nf["object_id"]: NotionCRM.build_text(object_id),
        nf["status"]: NotionCRM.build_status(statuses["after_structurize"]),
        # Адрес: приоритет — блок «Где вы будете» из объявления Airbnb
        nf["address"]: NotionCRM.build_text(listing_address or maps.address),
        nf["district"]: NotionCRM.build_text(draft.district),
        nf["google_maps"]: NotionCRM.build_url(maps.url),
        nf["description"]: NotionCRM.build_text(description),
        nf["caption_tg"]: NotionCRM.build_text(caption_tg),
        nf["caption_fb"]: NotionCRM.build_text(caption_fb),
        nf["last_error"]: NotionCRM.build_text(""),
    }

    source_url = extract_source_url(source)
    if source_url:
        properties[nf["source"]] = NotionCRM.build_url(source_url)
    elif draft.source_note and "источник:" not in description.lower():
        properties[nf["description"]] = NotionCRM.build_text(
            f"{description}\n\nИсточник: {draft.source_note}".strip()
        )

    if gallery_url:
        properties[nf["photo"]] = NotionCRM.build_url(gallery_url)
    if draft.rooms is not None:
        properties[nf.get("rooms", "Количество комнат")] = NotionCRM.build_number(draft.rooms)
    if draft.bathrooms is not None:
        properties[nf.get("bathrooms", "Количество сан.узлов")] = NotionCRM.build_number(draft.bathrooms)
    if draft.area is not None:
        properties[nf.get("area", "Площадь участка (м²)")] = NotionCRM.build_number(draft.area)
    if draft.price_monthly is not None:
        properties[nf.get("price_monthly", "Цена за месяц")] = NotionCRM.build_number(draft.price_monthly)
    if draft.price_yearly is not None:
        properties[nf.get("price_yearly", "Цена за год")] = NotionCRM.build_number(draft.price_yearly)
    if draft.deposit is not None:
        properties[nf.get("deposit", "Залог")] = NotionCRM.build_number(draft.deposit)
    if draft.housing_type:
        properties[nf["housing_type"]] = NotionCRM.build_select(draft.housing_type)
    if draft.view:
        properties[nf.get("view", "Вид")] = NotionCRM.build_select(draft.view)
    if draft.rent_type:
        properties[nf.get("rent_type", "Тип аренды")] = NotionCRM.build_select(draft.rent_type)
    if draft.amenities:
        properties[nf.get("amenities", "Удобства")] = NotionCRM.build_multi_select(draft.amenities)

    return properties


def main() -> int:
    load_dotenv()
    cfg = load_config()
    nf = cfg["notion"]["fields"]
    statuses = cfg["notion"]["statuses"]

    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--source", default="Telegram")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-new-id", action="store_true", help="Ignore session.json (debug only)")
    args = parser.parse_args()

    session_path = ROOT / "data" / "sessions" / args.session
    desc_path = session_path / "description.txt"
    photos_dir = session_path / "photos"

    if not desc_path.exists():
        print("ERROR: no description.txt", file=sys.stderr)
        return 1

    photos = sorted(
        p for p in photos_dir.iterdir()
        if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if not photos:
        print("ERROR: no photos in session", file=sys.stderr)
        return 1

    description = desc_path.read_text(encoding="utf-8").strip()
    # Страховка: Airbnb иногда отдаёт описание без автоперевода (язык хозяина,
    # например китайский) — переводим сами, иначе регэкспы и Notion получат CJK.
    if cjk_ratio(description) > 0.15:
        translated = translate_to_russian(description)
        if translated and cjk_ratio(translated) < 0.05:
            print("NOTE: описание было не на русском — переведено LLM", file=sys.stderr)
            description = translated
            desc_path.write_text(description + "\n", encoding="utf-8")
    crm = NotionCRM(
        os.environ["NOTION_API_KEY"],
        os.environ.get("NOTION_DB_ID") or os.environ["NOTION_DATABASE_ID"],
    )

    object_id: str | None = None
    notion_page_id: str | None = load_session(args.session).get("notion_page_id")

    try:
        source_prefix = detect_source_prefix(args.session, args.source, session_path)
        object_id = resolve_object_id(
            args.session, crm, force_new=args.force_new_id, source=source_prefix
        )
        log_event(object_id, "agent2", "start", session=args.session, photos=len(photos))

        draft = parse_listing(
            description,
            districts=cfg["phuket_districts"],
            known_projects=cfg.get("known_projects"),
            source=args.source,
            fx=cfg.get("fx"),
        )
        parsed_meta = load_parsed_meta(session_path)
        maps = resolve_google_maps(
            description,
            draft.district,
            draft.title,
            known_projects=cfg.get("known_projects"),
            coords=extract_coords(parsed_meta),
        )
        # Район из координат карты (Google Maps) приоритетнее текстового
        if maps.district:
            draft.district = maps.district
        draft.housing_type = detect_housing_type(
            description,
            title=draft.title,
            complex_name=maps.complex_name,
            known_projects=cfg.get("known_projects"),
        ) or draft.housing_type

        r2 = CloudflareR2(
            os.environ["CLOUDFLARE_ENDPOINT"],
            os.environ["CLOUDFLARE_BUCKET"],
            os.environ["CLOUDFLARE_ACCESS_KEY_ID"],
            os.environ["CLOUDFLARE_SECRET_ACCESS_KEY"],
            os.environ["CLOUDFLARE_PUBLIC_BASE_URL"],
        )

        gallery_base = f"{object_id}/photos"
        gallery_url: str | None = None
        uploaded: list[str] = []

        if not args.dry_run:
            # Параллельная загрузка в R2 (_signed_put без общего состояния — thread-safe);
            # порядок photo_001..N сохраняем по индексу.
            from concurrent.futures import ThreadPoolExecutor

            r2_workers = max(1, int(os.environ.get("R2_UPLOAD_WORKERS", "6")))
            photo_names = [
                f"photo_{i:03d}{photo.suffix.lower()}" for i, photo in enumerate(photos, 1)
            ]

            def _up(args_):
                photo, name = args_
                return r2.upload_from_path(str(photo), f"{gallery_base}/{name}")

            with ThreadPoolExecutor(max_workers=r2_workers) as pool:
                uploaded = list(pool.map(_up, zip(photos, photo_names)))
            log_event(object_id, "agent2", "r2_upload", count=len(uploaded))

            gallery_html = build_gallery_html(
                title=draft.title,
                object_id=object_id,
                photo_names=photo_names,
            )
            gallery_url = r2.upload_bytes(
                gallery_html.encode("utf-8"),
                f"{gallery_base}/index.html",
                "text/html; charset=utf-8",
            )
        else:
            gallery_url = r2.get_public_url(f"{gallery_base}/index.html")

        # Описания: LLM по промптам (config/prompts/) + обязательный
        # валидатор длины. Один длинный текст для TG и FB, короткий — соц.сети.
        desc_ctx = build_context(
            draft=draft,
            description=description,
            object_id=object_id,
            contacts=cfg.get("contacts", {}),
            parsed_meta=parsed_meta,
            complex_name=maps.complex_name,
            region=(cfg.get("region") or {}).get("name_ru", "Пхукет"),
        )
        caption_long = generate_long_description(desc_ctx)
        caption_tg = caption_long
        caption_fb = caption_long
        caption_social = generate_social_description(desc_ctx)

        properties = build_notion_properties(
            draft=draft,
            maps=maps,
            nf=nf,
            statuses=statuses,
            object_id=object_id,
            description=description,
            gallery_url=gallery_url,
            caption_tg=caption_tg,
            caption_fb=caption_fb,
            source=args.source,
            listing_address=extract_listing_address(parsed_meta),
        )
        apply_parsed_meta(properties, parsed_meta, nf)

        if nf.get("caption_social"):
            properties[nf["caption_social"]] = NotionCRM.build_text(caption_social)
        # «Вместимость гостей» — только явное число от хозяина;
        # формула комнаты×2+1 живёт лишь внутри текстов, в таблицу не пишется.
        if nf.get("max_guests") and desc_ctx["max_guests_explicit"]:
            properties[nf["max_guests"]] = NotionCRM.build_number(desc_ctx["max_guests"])

        result = {
            "object_id": object_id,
            "session_id": args.session,
            "title": draft.title,
            "complex_name": maps.complex_name,
            "district": draft.district,
            "address": extract_listing_address(parsed_meta) or maps.address,
            "google_maps": maps.url,
            "google_maps_query": maps.query,
            "google_maps_method": maps.method,
            "google_maps_place": maps.place_name,
            "photos_uploaded": len(uploaded) if uploaded else len(photos),
            "gallery_url": gallery_url,
            "housing_type": draft.housing_type,
            "view": draft.view,
            "rent_type": draft.rent_type,
            "amenities": draft.amenities,
            "status": statuses["after_structurize"],
        }

        if args.dry_run:
            result["dry_run"] = True
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0

        existing = crm.query_by_object_id(object_id)
        if existing:
            page = crm.update_page(existing["id"], properties)
        else:
            page = crm.create_page(properties)

        notion_page_id = page["id"]
        save_session(
            args.session,
            {
                "object_id": object_id,
                "notion_page_id": notion_page_id,
                "gallery_url": gallery_url,
                "title": draft.title,
            },
        )
        log_event(object_id, "agent2", "notion_saved", page_id=notion_page_id)

        if cfg.get("chain", {}).get("auto_continue_after_agent2", True):
            import subprocess as sp
            print("\n[chain] Agent 2 done → triggering Agent 3 via chain_runner")
            chain_rc = sp.run(
                [sys.executable, str(ROOT / "scripts" / "chain_runner.py"),
                 "--from-agent", "3", "--object-id", object_id],
                cwd=ROOT,
            ).returncode
            if chain_rc != 0:
                print(f"WARN: chain_runner exited {chain_rc}", file=sys.stderr)

        result["notion_page_id"] = notion_page_id
        result["next"] = (
            f"cd agent_3_director && node scripts/run_from_notion.mjs --object-id {object_id}"
        )
        print("READY_FOR_VIDEO")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        print(f"ERROR: {err}", file=sys.stderr)
        traceback.print_exc()
        if object_id:
            log_event(object_id, "agent2", "failed", error=err)
            try:
                page = crm.query_by_object_id(object_id) if object_id else None
                if page:
                    crm.set_error(page["id"], page, err)
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
