from __future__ import annotations

from ..app.config import AvailabilityConfig
from ..app.error_notify import handle_facebook_check_outcome
from ..app.models import MonthAvailability, MonthWindowItem, PropertySource
from .airbnb import LiveProviderDisabled
from .facebook_browser import fetch_marketplace_page
from .facebook_checker import FacebookCheckResult, classify_marketplace_page, extract_listing_id


class FacebookAvailabilityProvider:
    name = "FACEBOOK"
    requests_made = 0

    def fetch_month_availability(
        self,
        source: PropertySource,
        window: list[MonthWindowItem],
    ) -> list[MonthAvailability]:
        raise LiveProviderDisabled(
            "Facebook has no monthly availability — use check_listing_status()"
        )

    def check_listing_status(
        self,
        source: PropertySource,
        config: AvailabilityConfig,
    ) -> FacebookCheckResult:
        url = (source.source_url or "").strip()
        if not url:
            return FacebookCheckResult(
                outcome="UNCLASSIFIED",
                status_reason="empty_source_url",
            )
        listing_id = extract_listing_id(url) or ""
        try:
            final_url, html, body = fetch_marketplace_page(url, config)
            self.requests_made += 1
            result = classify_marketplace_page(
                url=final_url,
                html=html,
                body_text=body,
                listing_id=listing_id or None,
            )
            handle_facebook_check_outcome(result.outcome, result.status_reason)
            return result
        except Exception as exc:
            self.requests_made += 1
            msg = str(exc).lower()
            if "timeout" in msg:
                outcome = "TIMEOUT"
            elif "session missing" in msg or "login" in msg:
                outcome = "LOGIN_REQUIRED"
            else:
                outcome = "BROWSER_ERROR"
            result = FacebookCheckResult(
                outcome=outcome,
                status_reason=str(exc)[:500],
                listing_id=listing_id,
                page_url=url,
            )
            if outcome == "LOGIN_REQUIRED":
                handle_facebook_check_outcome(outcome, result.status_reason)
            return result
