from __future__ import annotations

from enum import Enum


class MarketplaceState(str, Enum):
    OPEN_MARKETPLACE = "OPEN_MARKETPLACE"
    CREATE_LISTING = "CREATE_LISTING"
    SELECT_LISTING_TYPE = "SELECT_LISTING_TYPE"
    UPLOAD_MEDIA = "UPLOAD_MEDIA"
    FILL_TITLE = "FILL_TITLE"
    FILL_PRICE = "FILL_PRICE"
    SELECT_CATEGORY = "SELECT_CATEGORY"
    SELECT_CONDITION = "SELECT_CONDITION"
    FILL_DESCRIPTION = "FILL_DESCRIPTION"
    SET_LOCATION = "SET_LOCATION"
    COMPOSER_FORM = "COMPOSER_FORM"
    LEGACY_FORM = "LEGACY_FORM"
    REVIEW = "REVIEW"
    PUBLISH_CONFIRMATION = "PUBLISH_CONFIRMATION"
    VERIFY_SUCCESS = "VERIFY_SUCCESS"
    UNKNOWN_SCREEN = "UNKNOWN_SCREEN"
    BLOCKED_CHECKPOINT = "BLOCKED_CHECKPOINT"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


# Actions allowed per state (composer rent flow).
ALLOWED_ACTIONS: dict[MarketplaceState, frozenset[str]] = {
    MarketplaceState.OPEN_MARKETPLACE: frozenset(
        {"open_sell", "open_create_listing", "detect_only"}
    ),
    MarketplaceState.CREATE_LISTING: frozenset(
        {"select_housing", "select_rent", "detect_only"}
    ),
    MarketplaceState.SELECT_LISTING_TYPE: frozenset(
        {"select_housing", "select_rent", "detect_only"}
    ),
    MarketplaceState.UPLOAD_MEDIA: frozenset(
        {"open_gallery", "select_photo", "confirm_photos", "detect_only"}
    ),
    MarketplaceState.FILL_TITLE: frozenset(
        {"fill_field", "read_field", "scroll", "detect_only"}
    ),
    MarketplaceState.FILL_PRICE: frozenset(
        {"fill_field", "read_field", "scroll", "detect_only"}
    ),
    MarketplaceState.FILL_DESCRIPTION: frozenset(
        {"fill_field", "read_field", "clear_tags", "scroll", "detect_only"}
    ),
    MarketplaceState.SET_LOCATION: frozenset(
        {"open_location", "search_location", "pick_suggestion", "apply_location", "detect_only"}
    ),
    MarketplaceState.COMPOSER_FORM: frozenset(
        {
            "fill_field",
            "read_field",
            "open_gallery",
            "select_photo",
            "confirm_photos",
            "open_location",
            "search_location",
            "pick_suggestion",
            "apply_location",
            "clear_tags",
            "scroll",
            "go_next",
            "detect_only",
        }
    ),
    MarketplaceState.LEGACY_FORM: frozenset(
        {"fill_field", "read_field", "open_gallery", "select_photo", "detect_only"}
    ),
    MarketplaceState.REVIEW: frozenset(
        {"read_field", "validate_content", "go_next", "detect_only"}
    ),
    MarketplaceState.PUBLISH_CONFIRMATION: frozenset(
        {"publish", "validate_content", "detect_only"}
    ),
    MarketplaceState.VERIFY_SUCCESS: frozenset({"detect_only"}),
    MarketplaceState.UNKNOWN_SCREEN: frozenset({"detect_only", "handle_known_popup"}),
    MarketplaceState.BLOCKED_CHECKPOINT: frozenset({"detect_only"}),
    MarketplaceState.FAILED: frozenset({"detect_only"}),
    MarketplaceState.COMPLETED: frozenset({"detect_only"}),
}


FORBIDDEN_ACTIONS_GLOBAL = frozenset(
    {
        "publish_on_unknown",
        "fill_on_unknown",
        "coordinate_click_without_node",
        "bypass_captcha",
        "promote_boost",
    }
)
