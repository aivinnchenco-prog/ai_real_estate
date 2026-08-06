from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import PublishJob
from .snapshot import UISnapshot
from .validator import (
    FieldValidation,
    validate_description_field,
    validate_price_field,
    validate_title_field,
)


@dataclass(frozen=True)
class ContentValidationResult:
    ok: bool
    errors: tuple[str, ...]
    checks: tuple[str, ...]


def _normalize_digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def validate_listing_content(
    job: PublishJob,
    snapshot: UISnapshot,
    *,
    ui_cfg: dict[str, Any],
    title_value: str,
    price_value: str,
    description_value: str,
    media_count: int,
    expected_media_count: int,
    location_value: str = "",
) -> ContentValidationResult:
    errors: list[str] = []
    checks: list[str] = []

    expected_title = (job.title or "").strip()[:100]
    expected_price = _normalize_digits(str(int(round(job.listing.price_monthly or 0))))
    expected_description = (job.caption_fb or "").strip()

    if expected_title and title_value.strip() != expected_title:
        errors.append("title_mismatch")
    else:
        checks.append("title_ok")

    if expected_price and _normalize_digits(price_value) != expected_price:
        errors.append("price_mismatch")
    else:
        checks.append("price_ok")

    if expected_description:
        if not description_value.strip():
            errors.append("description_empty")
        elif expected_description not in description_value and description_value not in expected_description:
            errors.append("description_mismatch")
        else:
            checks.append("description_ok")

    if media_count < expected_media_count:
        errors.append("media_count_low")
    else:
        checks.append("media_ok")

    if location_value and job.object_id and job.object_id in location_value:
        errors.append("location_contains_object_id")
    elif location_value:
        checks.append("location_ok")

    title_val = validate_title_field(snapshot, ui_cfg=ui_cfg)
    price_val = validate_price_field(snapshot, ui_cfg=ui_cfg)
    desc_val = validate_description_field(snapshot, ui_cfg=ui_cfg)
    if not title_val.ok:
        errors.append("title_field_semantic_failed")
    if not price_val.ok:
        errors.append("price_field_semantic_failed")
    if expected_description and not desc_val.ok:
        errors.append("description_field_semantic_failed")

    return ContentValidationResult(
        ok=not errors,
        errors=tuple(errors),
        checks=tuple(checks),
    )
