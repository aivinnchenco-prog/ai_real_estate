from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import load_marketplace_selectors
from .detector import detect_marketplace_state
from .selectors import SelectorMatch, find_element, find_labeled_input
from .snapshot import UISnapshot
from .states import MarketplaceState


@dataclass(frozen=True)
class FieldValidation:
    field: str
    ok: bool
    confidence: float
    evidence: tuple[str, ...]
    match: SelectorMatch | None = None


def _is_tags_node(node_label: str, y1: int, tags_y_min: int) -> bool:
    low = (node_label or "").lower()
    if "тег" in low or "tag" in low:
        return True
    return y1 >= tags_y_min


def validate_price_field(
    snapshot: UISnapshot,
    *,
    ui_cfg: dict[str, Any],
    state: MarketplaceState | None = None,
) -> FieldValidation:
    selectors = load_marketplace_selectors()
    match = find_element(snapshot, "price_field", selectors=selectors, require_editable=True)
    if match is None:
        match = find_labeled_input(
            snapshot,
            label_aliases=["Цена", "Price", "Стоимость"],
            resource_id="marketplace_composer_price_input",
        )
    evidence: list[str] = []
    confidence = 0.0
    if match:
        confidence = match.confidence
        evidence.extend(match.evidence)
        node = match.node
        if "number" in node.input_type.lower() or "marketplace_composer_price_input" in node.resource_id:
            confidence = min(1.0, confidence + 0.04)
            evidence.append("input_type=numeric")
    if state in {MarketplaceState.FILL_PRICE, MarketplaceState.COMPOSER_FORM, MarketplaceState.REVIEW}:
        confidence = min(1.0, confidence + 0.02)
        evidence.append(f"state={state.value}")
    ok = match is not None and confidence >= 0.7
    return FieldValidation(
        field="price",
        ok=ok,
        confidence=confidence,
        evidence=tuple(evidence),
        match=match,
    )


def validate_title_field(
    snapshot: UISnapshot,
    *,
    ui_cfg: dict[str, Any],
    state: MarketplaceState | None = None,
) -> FieldValidation:
    selectors = load_marketplace_selectors()
    match = find_labeled_input(
        snapshot,
        label_aliases=["Название", "Title", "Заголовок"],
        forbidden_labels=["Описание", "Description", "Теги", "Tags", "Цена", "Price"],
        y_max=ui_cfg.get("field_bands", {}).get("description_y_min", 620),
    )
    evidence: list[str] = []
    confidence = 0.0
    if match:
        confidence = match.confidence
        evidence.extend(match.evidence)
        if match.node.resource_id == "marketplace_composer_price_input":
            return FieldValidation("title", False, 0.0, ("forbidden=price_input",), None)
    if state in {MarketplaceState.FILL_TITLE, MarketplaceState.COMPOSER_FORM, MarketplaceState.REVIEW}:
        confidence = min(1.0, confidence + 0.02)
        evidence.append(f"state={state.value}")
    ok = match is not None and confidence >= 0.7
    return FieldValidation("title", ok, confidence, tuple(evidence), match)


def validate_description_field(
    snapshot: UISnapshot,
    *,
    ui_cfg: dict[str, Any],
    state: MarketplaceState | None = None,
) -> FieldValidation:
    bands = ui_cfg.get("field_bands", {})
    y_min = int(bands.get("description_y_min", 620))
    y_max = int(bands.get("description_y_max", 950))
    tags_y_min = int(bands.get("tags_y_min", 960))
    match = find_labeled_input(
        snapshot,
        label_aliases=["Описание", "Description"],
        forbidden_labels=["Теги", "Tags", "Название", "Title", "Цена", "Price"],
        y_min=y_min,
        y_max=y_max,
    )
    evidence: list[str] = []
    confidence = 0.0
    if match and _is_tags_node(match.node.label, match.node.y1, tags_y_min):
        return FieldValidation("description", False, 0.0, ("matched_tags_field",), None)
    if match:
        confidence = match.confidence
        evidence.extend(match.evidence)
        evidence.append(f"y_band={y_min}-{y_max}")
    if state in {
        MarketplaceState.FILL_DESCRIPTION,
        MarketplaceState.COMPOSER_FORM,
        MarketplaceState.REVIEW,
    }:
        confidence = min(1.0, confidence + 0.02)
        evidence.append(f"state={state.value}")
    ok = match is not None and confidence >= 0.7
    return FieldValidation("description", ok, confidence, tuple(evidence), match)


def validate_field_for_target(
    snapshot: UISnapshot,
    target: str,
    *,
    ui_cfg: dict[str, Any],
    state: MarketplaceState | None = None,
) -> FieldValidation:
    if target == "price":
        return validate_price_field(snapshot, ui_cfg=ui_cfg, state=state)
    if target == "title":
        return validate_title_field(snapshot, ui_cfg=ui_cfg, state=state)
    if target == "description":
        return validate_description_field(snapshot, ui_cfg=ui_cfg, state=state)
    return FieldValidation(target, False, 0.0, ("unknown_target",), None)
