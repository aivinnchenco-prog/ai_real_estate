"""Facebook Marketplace ACTIVE/SOLD classification.

Business rules (rentals):
- Visible «Rented» badge → SOLD
- Listing loads, badge absent → ACTIVE (default)
- Relay is_sold in page JSON used when present (corroboration / fallback)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Literal

BusinessStatus = Literal["ACTIVE", "SOLD"]

# Reference listing IDs from controlled verification (do not change).
REFERENCE_ACTIVE_ITEM_ID = "2688776784851158"
REFERENCE_SOLD_ITEM_ID = "1532266511962787"
REFERENCE_ACTIVE_URL = "https://www.facebook.com/share/1F2qZgze3t/?mibextid=wwXIfr"
REFERENCE_SOLD_URL = "https://www.facebook.com/share/19KfDfdc9m/?mibextid=wwXIfr"

_ITEM_ID_PATTERNS = (
    re.compile(r"facebook\.com/marketplace/item/(\d+)", re.I),
    re.compile(r'"id"\s*:\s*"(\d{8,})"', re.I),
)

# Standalone «Rented» badge (not seller description prose).
_RENTED_BADGE_BODY_RE = re.compile(r"(?:^|[\s\n])Rented(?:$|[\s\n])", re.I | re.MULTILINE)
_RENTED_BADGE_HTML_RE = re.compile(
    r'"(?:text|label|title)":"Rented"|marketplace_listing_status[^}]*Rented',
    re.I,
)


class FacebookTechnicalOutcome(str, Enum):
    TIMEOUT = "TIMEOUT"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    ACCESS_DENIED = "ACCESS_DENIED"
    PAGE_UNAVAILABLE = "PAGE_UNAVAILABLE"
    BROWSER_ERROR = "BROWSER_ERROR"
    UNCLASSIFIED = "UNCLASSIFIED"


@dataclass(frozen=True)
class FacebookCheckResult:
    """Classifier result. Business status only when outcome is SUCCESS."""

    outcome: str  # ACTIVE | SOLD | technical enum value
    status_reason: str = ""
    listing_id: str = ""
    is_sold: bool | None = None
    is_live: bool | None = None
    is_pending: bool | None = None
    page_url: str = ""
    listing_loaded: bool = False

    @property
    def is_business(self) -> bool:
        return self.outcome in ("ACTIVE", "SOLD")

    @property
    def is_technical_error(self) -> bool:
        return not self.is_business

    @property
    def business_status(self) -> BusinessStatus | None:
        if self.outcome in ("ACTIVE", "SOLD"):
            return self.outcome
        return None


def extract_listing_id(url: str, html: str | None = None) -> str | None:
    for pat in _ITEM_ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    if html:
        m = re.search(r"/marketplace/item/(\d+)", html)
        if m:
            return m.group(1)
    return None


def _login_required(url: str, html: str, body_text: str) -> bool:
    low_url = url.lower()
    if "/login" in low_url or "/checkpoint" in low_url:
        return True
    low = (html + "\n" + body_text).lower()
    markers = (
        "log into facebook",
        "log in to facebook",
        "email or mobile number",
        "email or phone",
    )
    return any(m in low for m in markers)


def _page_unavailable(html: str, body_text: str) -> bool:
    low = (html + "\n" + body_text).lower()
    markers = (
        "this listing isn't available",
        "this listing is no longer available",
        "listing isn't available",
        "no longer available",
        "content isn't available",
        "page isn't available",
    )
    return any(m in low for m in markers)


def _parse_relay_is_sold(html: str, listing_id: str) -> tuple[bool | None, bool | None, bool | None]:
    triple = re.search(
        r'"is_live":(true|false),"is_pending":(true|false),"is_sold":(true|false),"id":"' + listing_id + r'"',
        html,
        re.I,
    )
    if triple:
        return (
            triple.group(1).lower() == "true",
            triple.group(2).lower() == "true",
            triple.group(3).lower() == "true",
        )
    sold_only = re.search(
        r'"is_sold":(true|false),"id":"' + listing_id + r'"',
        html,
        re.I,
    )
    if sold_only:
        is_sold = sold_only.group(1).lower() == "true"
        return None, None, is_sold
    title_block = re.search(
        r'"marketplace_listing_title":"[^"]+","is_live":(true|false),'
        r'"is_pending":(true|false),"is_sold":(true|false),"id":"' + listing_id + r'"',
        html,
        re.I,
    )
    if title_block:
        return (
            title_block.group(1).lower() == "true",
            title_block.group(2).lower() == "true",
            title_block.group(3).lower() == "true",
        )
    return None, None, None


def _listing_page_loaded(url: str, html: str, listing_id: str) -> bool:
    low_url = url.lower()
    if f"/marketplace/item/{listing_id}" in low_url:
        if f'"id":"{listing_id}"' in html or "marketplace_listing_title" in html:
            return True
    return f'"id":"{listing_id}"' in html and "marketplace_listing_title" in html


def _has_rented_badge(html: str, body_text: str) -> bool:
    if _RENTED_BADGE_BODY_RE.search(body_text or ""):
        return True
    return _RENTED_BADGE_HTML_RE.search(html or "") is not None


def classify_marketplace_page(
    *,
    url: str,
    html: str,
    body_text: str,
    listing_id: str | None = None,
) -> FacebookCheckResult:
    """Classify listing: Rented badge → SOLD; otherwise ACTIVE when page loads."""
    lid = listing_id or extract_listing_id(url, html) or ""
    if _login_required(url, html, body_text):
        return FacebookCheckResult(
            outcome=FacebookTechnicalOutcome.LOGIN_REQUIRED.value,
            status_reason="redirect_or_login_form",
            listing_id=lid,
            page_url=url,
        )
    if _page_unavailable(html, body_text):
        return FacebookCheckResult(
            outcome=FacebookTechnicalOutcome.PAGE_UNAVAILABLE.value,
            status_reason="listing_unavailable_message",
            listing_id=lid,
            page_url=url,
        )
    if not lid:
        return FacebookCheckResult(
            outcome=FacebookTechnicalOutcome.UNCLASSIFIED.value,
            status_reason="listing_id_not_found",
            page_url=url,
        )

    is_live, is_pending, is_sold = _parse_relay_is_sold(html, lid)
    listing_loaded = _listing_page_loaded(url, html, lid)
    rented_badge = _has_rented_badge(html, body_text)

    if is_sold is not None:
        if is_sold or rented_badge:
            reason = "relay:is_sold=true"
            if rented_badge and not is_sold:
                reason = "relay:is_sold=true+ui:Rented"
            return FacebookCheckResult(
                outcome="SOLD",
                status_reason=reason,
                listing_id=lid,
                is_sold=True,
                is_live=is_live,
                is_pending=is_pending,
                page_url=url,
                listing_loaded=True,
            )
        if rented_badge:
            return FacebookCheckResult(
                outcome="SOLD",
                status_reason="ui:Rented",
                listing_id=lid,
                is_sold=True,
                page_url=url,
                listing_loaded=listing_loaded,
            )
        return FacebookCheckResult(
            outcome="ACTIVE",
            status_reason="relay:is_sold=false",
            listing_id=lid,
            is_sold=False,
            is_live=is_live,
            is_pending=is_pending,
            page_url=url,
            listing_loaded=True,
        )

    if rented_badge:
        return FacebookCheckResult(
            outcome="SOLD",
            status_reason="ui:Rented",
            listing_id=lid,
            is_sold=True,
            page_url=url,
            listing_loaded=listing_loaded,
        )

    if listing_loaded:
        return FacebookCheckResult(
            outcome="ACTIVE",
            status_reason="ui:no_rented_badge",
            listing_id=lid,
            is_sold=False,
            page_url=url,
            listing_loaded=True,
        )

    return FacebookCheckResult(
        outcome=FacebookTechnicalOutcome.UNCLASSIFIED.value,
        status_reason="listing_page_not_loaded",
        listing_id=lid,
        page_url=url,
        listing_loaded=False,
    )
