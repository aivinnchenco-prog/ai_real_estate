from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import load_marketplace_selectors
from .snapshot import UISnapshot


@dataclass(frozen=True)
class PopupMatch:
    popup_id: str
    label: str
    action: str


def detect_known_popup(snapshot: UISnapshot) -> PopupMatch | None:
    catalog = load_marketplace_selectors()
    popups = catalog.get("known_popups")
    if not isinstance(popups, dict):
        return None
    hay = snapshot.xml.lower()
    for popup_id, profile in popups.items():
        if not isinstance(profile, dict):
            continue
        texts = list(profile.get("texts") or [])
        for text in texts:
            if text.lower() in hay:
                return PopupMatch(
                    popup_id=str(popup_id),
                    label=text,
                    action=str(profile.get("action") or "dismiss"),
                )
    return None


def handle_known_popup(device, popup: PopupMatch) -> bool:
    if popup.action == "allow":
        for label in ("Разрешить", "Allow", "Только в этот раз", "While using the app"):
            if device(text=label).exists(timeout=0.5):
                device(text=label).click()
                return True
            if device(description=label).exists(timeout=0.3):
                device(description=label).click()
                return True
        return False
    if popup.action in {"dismiss_or_save", "cancel"}:
        for label in ("СОХРАНИТЬ ЧЕРНОВИК", "Save draft", "Отмена", "Cancel"):
            if device(text=label).exists(timeout=0.5):
                device(text=label).click()
                return True
    return False
