from __future__ import annotations

from .fingerprints import MARKETPLACE_FINGERPRINTS, fingerprint_score
from .snapshot import UISnapshot
from .states import MarketplaceState


def _is_composer_form(snapshot: UISnapshot) -> bool:
    return snapshot.text_present(
        "Новое объявление",
        "New listing",
        "marketplace_composer_price_input",
        "Название",
        "Title",
    )


def _is_location_map(snapshot: UISnapshot) -> bool:
    return snapshot.text_present("Поиск", "Search") and snapshot.text_present(
        "Применить",
        "Apply",
        "Map",
        "Карта",
    )


def _is_blocked(snapshot: UISnapshot) -> bool:
    return snapshot.text_present(
        "подтвердите, что вы",
        "confirm it's you",
        "captcha",
        "checkpoint",
        "временно ограничено",
        "policy",
    )


def _is_publish_screen(snapshot: UISnapshot) -> bool:
    return snapshot.text_present("Опубликовать", "Publish", "Разместить")


def detect_marketplace_state(snapshot: UISnapshot) -> MarketplaceState:
    if _is_blocked(snapshot):
        return MarketplaceState.BLOCKED_CHECKPOINT

    best_state = MarketplaceState.UNKNOWN_SCREEN
    best_score = 0.0
    for fingerprint in MARKETPLACE_FINGERPRINTS:
        score, _ = fingerprint_score(snapshot, fingerprint)
        if score >= fingerprint.min_confidence and score > best_score:
            best_score = score
            best_state = fingerprint.state

    if best_state != MarketplaceState.UNKNOWN_SCREEN:
        return best_state

    if _is_location_map(snapshot):
        return MarketplaceState.SET_LOCATION
    if _is_publish_screen(snapshot):
        return MarketplaceState.PUBLISH_CONFIRMATION
    if _is_composer_form(snapshot):
        return MarketplaceState.COMPOSER_FORM
    if snapshot.text_present("Создать объявление", "Create listing", "Продать", "Sell"):
        return MarketplaceState.CREATE_LISTING
    if snapshot.text_present("Далее", "Next") and snapshot.text_present("Фото", "Photo"):
        return MarketplaceState.UPLOAD_MEDIA

    return MarketplaceState.UNKNOWN_SCREEN
