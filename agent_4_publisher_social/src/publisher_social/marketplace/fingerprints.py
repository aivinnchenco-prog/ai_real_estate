from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .snapshot import UISnapshot
from .states import MarketplaceState


@dataclass(frozen=True)
class ScreenFingerprint:
    state: MarketplaceState
    required_any: tuple[dict[str, Any], ...]
    optional: tuple[dict[str, Any], ...] = ()
    forbidden: tuple[dict[str, Any], ...] = ()
    min_confidence: float = 0.7


def _has_text(snapshot: UISnapshot, text: str) -> bool:
    return text.lower() in snapshot.xml.lower()


def _has_numeric_input(snapshot: UISnapshot) -> bool:
    for node in snapshot.find_edittexts():
        if "number" in node.input_type.lower():
            return True
        if "marketplace_composer_price_input" in node.resource_id:
            return True
    return False


def _evaluate_rule(snapshot: UISnapshot, rule: dict[str, Any]) -> bool:
    if "text" in rule:
        return _has_text(snapshot, str(rule["text"]))
    if rule.get("numeric_input"):
        return _has_numeric_input(snapshot)
    if rule.get("resource_id"):
        rid = str(rule["resource_id"])
        return any(rid in node.resource_id for node in snapshot.nodes)
    if rule.get("content_desc_contains"):
        needle = str(rule["content_desc_contains"]).lower()
        return any(needle in node.content_desc.lower() for node in snapshot.nodes)
    return False


def fingerprint_score(snapshot: UISnapshot, fingerprint: ScreenFingerprint) -> tuple[float, list[str]]:
    evidence: list[str] = []
    if fingerprint.forbidden:
        for rule in fingerprint.forbidden:
            if _evaluate_rule(snapshot, rule):
                return 0.0, [f"forbidden:{rule}"]

    if not fingerprint.required_any:
        return 0.0, ["missing_required"]

    matched = 0
    for rule in fingerprint.required_any:
        if _evaluate_rule(snapshot, rule):
            matched += 1
            evidence.append(f"required:{rule}")
    required_ratio = matched / max(1, len(fingerprint.required_any))
    if matched == 0:
        return 0.0, evidence

    optional = 0
    for rule in fingerprint.optional:
        if _evaluate_rule(snapshot, rule):
            optional += 1
            evidence.append(f"optional:{rule}")
    optional_bonus = 0.05 * optional
    score = min(1.0, required_ratio * 0.85 + optional_bonus)
    return score, evidence


MARKETPLACE_FINGERPRINTS: tuple[ScreenFingerprint, ...] = (
    ScreenFingerprint(
        state=MarketplaceState.BLOCKED_CHECKPOINT,
        required_any=(
            {"text": "подтвердите, что вы"},
            {"text": "confirm it's you"},
            {"text": "captcha"},
            {"text": "checkpoint"},
        ),
        min_confidence=0.75,
    ),
    ScreenFingerprint(
        state=MarketplaceState.SET_LOCATION,
        required_any=(
            {"text": "Поиск"},
            {"text": "Search"},
        ),
        optional=({"text": "Применить"}, {"text": "Apply"}),
        min_confidence=0.7,
    ),
    ScreenFingerprint(
        state=MarketplaceState.UPLOAD_MEDIA,
        required_any=(
            {"text": "Далее"},
            {"text": "Next"},
            {"content_desc_contains": "Фото"},
            {"content_desc_contains": "Photo"},
        ),
        forbidden=({"text": "Опубликовать"}, {"text": "Publish"}),
        min_confidence=0.7,
    ),
    ScreenFingerprint(
        state=MarketplaceState.FILL_PRICE,
        required_any=(
            {"text": "Цена"},
            {"text": "Price"},
            {"numeric_input": True},
        ),
        forbidden=({"text": "Опубликовать"}, {"text": "Publish"}),
        min_confidence=0.72,
    ),
    ScreenFingerprint(
        state=MarketplaceState.FILL_DESCRIPTION,
        required_any=(
            {"text": "Описание"},
            {"text": "Description"},
        ),
        forbidden=({"text": "Теги"}, {"text": "Tags"}),
        min_confidence=0.72,
    ),
    ScreenFingerprint(
        state=MarketplaceState.COMPOSER_FORM,
        required_any=(
            {"text": "Новое объявление"},
            {"text": "New listing"},
            {"resource_id": "marketplace_composer_price_input"},
            {"text": "Название"},
            {"text": "Title"},
        ),
        min_confidence=0.68,
    ),
    ScreenFingerprint(
        state=MarketplaceState.PUBLISH_CONFIRMATION,
        required_any=(
            {"text": "Опубликовать"},
            {"text": "Publish"},
        ),
        min_confidence=0.75,
    ),
    ScreenFingerprint(
        state=MarketplaceState.CREATE_LISTING,
        required_any=(
            {"text": "Создать объявление"},
            {"text": "Create listing"},
            {"text": "Продать"},
            {"text": "Sell"},
        ),
        min_confidence=0.65,
    ),
)


def detect_ui_variant(
    previous: MarketplaceState | None,
    snapshot: UISnapshot,
) -> str | None:
    if previous is None:
        return None
    prev_fp = next((fp for fp in MARKETPLACE_FINGERPRINTS if fp.state == previous), None)
    if prev_fp is None:
        return None
    score, _ = fingerprint_score(snapshot, prev_fp)
    if score < prev_fp.min_confidence * 0.6:
        return "possible_ui_variant"
    return None
