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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from real_estate_handler import (  # noqa: E402
    CloudflareR2,
    DescriptionGenerator,
    NotionCRM,
    ObjectIDGenerator,
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
        return json.load(f)


def extract_source_url(source: str) -> str | None:
    m = re.search(r"https?://[^\s]+", source)
    return m.group(0).rstrip(".,)") if m else None


def resolve_object_id(session_id: str, crm: NotionCRM, force_new: bool = False) -> str:
    """Strictly one object_id per session."""
    if not force_new:
        existing = get_object_id(session_id)
        if existing:
            return existing
    object_id = ObjectIDGenerator.generate(crm)
    bind_object_id(session_id, object_id)
    return object_id


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
) -> dict:
    properties = {
        nf["title"]: NotionCRM.build_title(draft.title),
        nf["object_id"]: NotionCRM.build_text(object_id),
        nf["status"]: NotionCRM.build_status(statuses["after_structurize"]),
        nf["address"]: NotionCRM.build_text(maps.address),
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
    crm = NotionCRM(
        os.environ["NOTION_API_KEY"],
        os.environ.get("NOTION_DB_ID") or os.environ["NOTION_DATABASE_ID"],
    )

    object_id: str | None = None
    notion_page_id: str | None = load_session(args.session).get("notion_page_id")

    try:
        object_id = resolve_object_id(args.session, crm, force_new=args.force_new_id)
        log_event(object_id, "agent2", "start", session=args.session, photos=len(photos))

        draft = parse_listing(
            description,
            districts=cfg["phuket_districts"],
            known_projects=cfg.get("known_projects"),
            source=args.source,
        )
        maps = resolve_google_maps(
            description,
            draft.district,
            draft.title,
            known_projects=cfg.get("known_projects"),
        )
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
            photo_names: list[str] = []
            for i, photo in enumerate(photos, 1):
                name = f"photo_{i:03d}{photo.suffix.lower()}"
                dest = f"{gallery_base}/{name}"
                url = r2.upload_from_path(str(photo), dest)
                uploaded.append(url)
                photo_names.append(name)
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

        caption_tg = DescriptionGenerator.telegram(
            title=draft.title,
            description=description,
            price_monthly=draft.price_monthly,
            price_yearly=draft.price_yearly,
            rooms=draft.rooms,
            area=draft.area,
            object_id=object_id,
        )
        caption_fb = DescriptionGenerator.facebook(
            title=draft.title,
            description=description,
            price_monthly=draft.price_monthly,
            rooms=draft.rooms,
            area=draft.area,
            type_housing=draft.housing_type,
            object_id=object_id,
        )

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
        )

        result = {
            "object_id": object_id,
            "session_id": args.session,
            "title": draft.title,
            "complex_name": maps.complex_name,
            "district": draft.district,
            "address": maps.address,
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
        result["next"] = f"node scripts/agent3_video.mjs --object-id {object_id}"
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
