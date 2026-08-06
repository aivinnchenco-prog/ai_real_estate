from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import load_marketplace_selectors
from .snapshot import UINode, UISnapshot, bounds_center, parse_bounds


@dataclass(frozen=True)
class SelectorMatch:
    element_key: str
    node: UINode
    method: str
    confidence: float
    evidence: tuple[str, ...]


def _node_text_blob(node: UINode) -> str:
    return f"{node.text} {node.content_desc} {node.resource_id}".lower()


def _matches_any(value: str, aliases: list[str]) -> str | None:
    low = (value or "").lower()
    for alias in aliases:
        alias_low = alias.lower()
        if alias_low and alias_low in low:
            return alias
    return None


def find_by_profile(
    snapshot: UISnapshot,
    profile: dict[str, Any],
    *,
    element_key: str,
    require_editable: bool = False,
    require_clickable: bool = False,
) -> SelectorMatch | None:
    """Search nodes using selector priority: resource-id → content-desc → text → class."""
    resource_ids = list(profile.get("resource_ids") or [])
    content_descs = list(profile.get("content_descriptions") or profile.get("labels") or [])
    texts = list(profile.get("texts") or [])
    classes = list(profile.get("classes") or [])
    forbidden_resource_ids = set(profile.get("forbidden_resource_ids") or [])
    forbidden_labels = list(profile.get("forbidden_labels") or [])

    candidates: list[SelectorMatch] = []

    for node in snapshot.nodes:
        if require_editable and not node.editable and "edittext" not in node.class_name.lower():
            continue
        if require_clickable and not node.clickable:
            continue
        if node.resource_id in forbidden_resource_ids:
            continue
        if _matches_any(node.label, forbidden_labels):
            continue

        for rid in resource_ids:
            if rid and rid in node.resource_id:
                candidates.append(
                    SelectorMatch(
                        element_key=element_key,
                        node=node,
                        method="resource-id",
                        confidence=0.98,
                        evidence=(f"resource-id={rid}",),
                    )
                )
                break

        for desc in content_descs:
            if _matches_any(node.content_desc, [desc]):
                candidates.append(
                    SelectorMatch(
                        element_key=element_key,
                        node=node,
                        method="content-description",
                        confidence=0.94,
                        evidence=(f"content-desc~={desc}",),
                    )
                )
                break

        for text in texts:
            if _matches_any(node.text, [text]) or _matches_any(node.content_desc, [text]):
                candidates.append(
                    SelectorMatch(
                        element_key=element_key,
                        node=node,
                        method="text",
                        confidence=0.9,
                        evidence=(f"text~={text}",),
                    )
                )
                break

        for class_name in classes:
            if class_name and class_name in node.class_name:
                candidates.append(
                    SelectorMatch(
                        element_key=element_key,
                        node=node,
                        method="class",
                        confidence=0.75,
                        evidence=(f"class={class_name}",),
                    )
                )
                break

    if not candidates:
        return None
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return candidates[0]


def find_element(
    snapshot: UISnapshot,
    element_key: str,
    *,
    selectors: dict[str, Any] | None = None,
    require_editable: bool = False,
    require_clickable: bool = False,
) -> SelectorMatch | None:
    catalog = selectors or load_marketplace_selectors()
    profile = catalog.get(element_key)
    if not isinstance(profile, dict):
        return None
    return find_by_profile(
        snapshot,
        profile,
        element_key=element_key,
        require_editable=require_editable,
        require_clickable=require_clickable,
    )


def find_labeled_input(
    snapshot: UISnapshot,
    *,
    label_aliases: list[str],
    forbidden_labels: list[str] | None = None,
    y_min: int | None = None,
    y_max: int | None = None,
    resource_id: str | None = None,
) -> SelectorMatch | None:
    """Label-to-input relationship within composer form."""
    forbidden = [value.lower() for value in (forbidden_labels or [])]
    label_nodes: list[UINode] = []
    for node in snapshot.nodes:
        blob = _node_text_blob(node)
        if any(alias.lower() in blob for alias in label_aliases):
            if any(bad in blob for bad in forbidden):
                continue
            label_nodes.append(node)

    edit_nodes = snapshot.find_edittexts()
    if resource_id:
        for node in edit_nodes:
            if resource_id in node.resource_id:
                return SelectorMatch(
                    element_key="labeled_input",
                    node=node,
                    method="resource-id",
                    confidence=0.97,
                    evidence=(f"resource-id={resource_id}",),
                )

    for label in label_nodes:
        if not label.bounds:
            continue
        lx1, ly1, lx2, ly2 = label.bounds
        best: UINode | None = None
        best_dist = 10_000
        for node in edit_nodes:
            if not node.bounds:
                continue
            if y_min is not None and node.y1 < y_min:
                continue
            if y_max is not None and node.y1 > y_max:
                continue
            blob = _node_text_blob(node)
            if any(bad in blob for bad in forbidden):
                continue
            x1, y1, x2, y2 = node.bounds
            if y1 < ly1 - 40:
                continue
            if y1 > ly2 + 220:
                continue
            dist = abs(y1 - ly2) + abs(((x1 + x2) // 2) - ((lx1 + lx2) // 2))
            if dist < best_dist:
                best = node
                best_dist = dist
        if best:
            return SelectorMatch(
                element_key="labeled_input",
                node=best,
                method="label-to-input",
                confidence=0.93,
                evidence=(f"label={label.label}", f"distance={best_dist}"),
            )

    if y_min is not None or y_max is not None:
        for node in edit_nodes:
            if y_min is not None and node.y1 < y_min:
                continue
            if y_max is not None and node.y1 > y_max:
                continue
            blob = _node_text_blob(node)
            if any(bad in blob for bad in forbidden):
                continue
            return SelectorMatch(
                element_key="labeled_input",
                node=node,
                method="y-band",
                confidence=0.82,
                evidence=(f"y1={node.y1}",),
            )
    return None


def click_target_for_node(node: UINode) -> tuple[str, tuple[int, int] | None]:
    """Prefer node-based click; coordinates only as explicit fallback."""
    if node.bounds:
        return "bounds", bounds_center(node.bounds)
    return "none", None


def match_from_bounds(xml: str, bounds: tuple[int, int, int, int]) -> UINode | None:
    for node in parse_ui_nodes(xml):
        if node.bounds == bounds:
            return node
    return None
