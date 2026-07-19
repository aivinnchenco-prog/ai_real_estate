#!/usr/bin/env python3
"""Чтение записей Notion CRM для цепочки агентов."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from real_estate_handler import NotionCRM


@dataclass
class NotionListing:
    page_id: str
    object_id: str
    status: str
    title: str
    gallery_url: str | None
    video_vertical: str | None
    video_seedance: str | None
    montage_flag: str = ""   # «Монтаж»: ДА/НЕТ, "" = дефолт из конфига
    publish_flag: str = ""   # «Публикация»: ДА/НЕТ, "" = дефолт из конфига
    tg_post_url: str = ""    # post_url_telegram: пост-витрина в TG-канале

    @property
    def has_videos(self) -> bool:
        return bool(self.video_vertical or self.video_seedance)


_FLAG_OFF = {"НЕТ", "NO", "NET", "FALSE", "0"}


def flag_enabled(value: str | None, default: bool = True) -> bool:
    """ДА/НЕТ-флаг из select-колонки: пустое значение = default (из pipeline.json)."""
    if not value or not value.strip():
        return default
    return value.strip().upper() not in _FLAG_OFF


def _rich_text(prop: dict) -> str:
    items = prop.get("rich_text") or []
    return "".join(t.get("plain_text", "") for t in items).strip()


def _title(prop: dict) -> str:
    items = prop.get("title") or []
    return "".join(t.get("plain_text", "") for t in items).strip()


def _select(prop: dict) -> str:
    return ((prop or {}).get("select") or {}).get("name") or ""


def parse_listing_page(page: dict[str, Any], fields: dict[str, str]) -> NotionListing | None:
    props = page.get("properties", {})
    object_id = _rich_text(props.get(fields["object_id"], {}))
    if not object_id:
        return None
    status_prop = props.get(fields["status"], {})
    status = (status_prop.get("status") or {}).get("name") or ""
    return NotionListing(
        page_id=page["id"],
        object_id=object_id,
        status=status,
        title=_title(props.get(fields["title"], {})),
        gallery_url=(props.get(fields["photo"], {}) or {}).get("url"),
        video_vertical=(props.get(fields["video_vertical"], {}) or {}).get("url"),
        video_seedance=(props.get(fields["video_seedance"], {}) or {}).get("url"),
        montage_flag=_select(props.get(fields.get("montage", "Монтаж"), {})),
        publish_flag=_select(props.get(fields.get("publish", "Публикация"), {})),
        tg_post_url=(props.get(fields.get("tg_post", "post_url_telegram"), {}) or {}).get("url") or "",
    )


def fetch_by_status(crm: NotionCRM, status: str, fields: dict[str, str], limit: int = 20) -> list[NotionListing]:
    out: list[NotionListing] = []
    for page in crm.query_by_status(status, limit=limit):
        listing = parse_listing_page(page, fields)
        if listing:
            out.append(listing)
    return out


def fetch_by_object_id(crm: NotionCRM, object_id: str, fields: dict[str, str]) -> NotionListing | None:
    page = crm.query_by_object_id(object_id)
    if not page:
        return None
    return parse_listing_page(page, fields)


def agent3_ready(
    listing: NotionListing,
    statuses: dict[str, str],
    *,
    allow_retry: bool = True,
    default_montage: bool = False,
) -> tuple[bool, str]:
    """Agent 3: ready_for_video + галерея + явный «Монтаж»=ДА, без готовых видео."""
    if not flag_enabled(listing.montage_flag, default_montage):
        return False, "«Монтаж» = НЕТ (монтаж выключен)"
    ok_status = {statuses["after_structurize"]}
    if allow_retry:
        ok_status.add(statuses.get("video_failed", "video_failed"))
    if listing.status not in ok_status:
        return False, f"status={listing.status}, need {statuses['after_structurize']}"
    if not listing.gallery_url:
        return False, "missing gallery URL in Notion (Фото)"
    if listing.has_videos and listing.status != statuses.get("video_failed", "video_failed"):
        return False, "videos already exist in Notion"
    return True, "ok"


def agent6_ready(
    listing: NotionListing,
    statuses: dict[str, str],
    *,
    default_publish: bool = False,
    default_montage: bool = False,
) -> tuple[bool, str]:
    """Agent 4/6 Publisher: флаг «Публикация» + ready_to_post + видео.

    Исключение: монтаж выключен («Монтаж» = НЕТ) — публикуем
    карусель без видео, поэтому наличие video_url не требуем.
    """
    if not flag_enabled(listing.publish_flag, default_publish):
        return False, "«Публикация» = НЕТ (постинг выключен)"
    if listing.status != statuses["video_done"]:
        return False, f"status={listing.status}, need {statuses['video_done']}"
    if listing.has_videos:
        return True, "ok"
    if not flag_enabled(listing.montage_flag, default_montage):
        if not listing.gallery_url:
            return False, "монтаж выключен, но нет галереи (Фото) для карусели"
        return True, "ok: карусель без видео (монтаж выключен)"
    return False, "no video_url_Seedance in Notion — wait for Agent 5 (or Agent 3 fallback)"


# Backward compatibility (deprecated)
agent4_ready = agent6_ready
