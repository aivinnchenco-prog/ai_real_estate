"""Unit tests for Facebook Marketplace relay is_sold classification."""
from availability_service.providers.facebook_checker import (
    REFERENCE_ACTIVE_ITEM_ID,
    REFERENCE_SOLD_ITEM_ID,
    classify_marketplace_page,
)


def _relay_html(item_id: str, is_sold: bool) -> str:
    sold = "true" if is_sold else "false"
    return (
        f'<html><script>"marketplace_listing_title":"Test listing",'
        f'"is_live":true,"is_pending":false,"is_sold":{sold},"id":"{item_id}"</script></html>'
    )


def test_active_reference_relay_signal():
    html = _relay_html(REFERENCE_ACTIVE_ITEM_ID, is_sold=False)
    result = classify_marketplace_page(
        url=f"https://www.facebook.com/marketplace/item/{REFERENCE_ACTIVE_ITEM_ID}",
        html=html,
        body_text="Message",
        listing_id=REFERENCE_ACTIVE_ITEM_ID,
    )
    assert result.outcome == "ACTIVE"
    assert result.is_sold is False
    assert result.status_reason == "relay:is_sold=false"


def test_sold_reference_relay_signal():
    html = _relay_html(REFERENCE_SOLD_ITEM_ID, is_sold=True)
    result = classify_marketplace_page(
        url=f"https://www.facebook.com/marketplace/item/{REFERENCE_SOLD_ITEM_ID}",
        html=html,
        body_text="Rented",
        listing_id=REFERENCE_SOLD_ITEM_ID,
    )
    assert result.outcome == "SOLD"
    assert result.is_sold is True
    assert result.status_reason == "relay:is_sold=true"


def test_login_required_not_sold():
    result = classify_marketplace_page(
        url="https://www.facebook.com/login/",
        html="<title>Log in to Facebook</title>",
        body_text="Email or phone",
    )
    assert result.outcome == "LOGIN_REQUIRED"
    assert result.business_status is None


def test_technical_error_not_maps_to_sold():
    result = classify_marketplace_page(
        url="https://www.facebook.com/marketplace/item/1234567890123456",
        html="<html>no listing state</html>",
        body_text="",
        listing_id="1234567890123456",
    )
    assert result.outcome == "UNCLASSIFIED"
    assert result.business_status is None


def test_active_when_no_relay_but_listing_loaded():
    lid = "993184816388436"
    html = (
        f'<html><script>"marketplace_listing_title":"Tanode Villa",'
        f'"id":"{lid}"</script></html>'
    )
    result = classify_marketplace_page(
        url=f"https://www.facebook.com/marketplace/item/{lid}/",
        html=html,
        body_text="Message Send message",
        listing_id=lid,
    )
    assert result.outcome == "ACTIVE"
    assert result.status_reason == "ui:no_rented_badge"


def test_sold_rented_badge_without_relay():
    lid = "9999999999999999"
    result = classify_marketplace_page(
        url=f"https://www.facebook.com/marketplace/item/{lid}/",
        html=f'"marketplace_listing_title":"Villa","id":"{lid}"',
        body_text="Rented\nMessage",
        listing_id=lid,
    )
    assert result.outcome == "SOLD"
    assert result.status_reason == "ui:Rented"


def test_checkpoint_word_in_html_not_login_required():
    """Relay JSON may contain 'checkpoint' — must not force LOGIN_REQUIRED."""
    html = _relay_html(REFERENCE_ACTIVE_ITEM_ID, is_sold=False) + "checkpoint token in json"
    result = classify_marketplace_page(
        url=f"https://www.facebook.com/marketplace/item/{REFERENCE_ACTIVE_ITEM_ID}",
        html=html,
        body_text="",
        listing_id=REFERENCE_ACTIVE_ITEM_ID,
    )
    assert result.outcome == "ACTIVE"


def test_78h_tier_hours():
    from availability_service.app.models import RefreshTier, TIER_HOURS

    assert TIER_HOURS[RefreshTier.H78] == 78


def test_facebook_tier_after_success():
    from availability_service.app.models import RefreshTier, SourceKind
    from availability_service.app.scheduler_policy import tier_after_success

    assert tier_after_success(RefreshTier.FIRST_REFRESH, SourceKind.FACEBOOK) == RefreshTier.H78
    assert tier_after_success(RefreshTier.H78, SourceKind.FACEBOOK) == RefreshTier.H78
    assert tier_after_success(RefreshTier.FIRST_REFRESH, SourceKind.AIRBNB) == RefreshTier.H48
