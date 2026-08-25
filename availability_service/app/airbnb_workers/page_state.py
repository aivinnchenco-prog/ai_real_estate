"""Airbnb page classification before calendar parse."""

from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import CheckResult

_CAPTCHA_URL = re.compile(r"captcha|challenge|account-security|verify", re.I)
_GENERIC_HOME = re.compile(
    r"^https?://(?:www\.)?airbnb\.(?:com|ru)/?(?:\?[^#]*)?$",
    re.I,
)
_NOT_FOUND = re.compile(
    r"page not found|404|this listing is no longer available|"
    r"listing.?s? (?:is )?unavailable|couldn.?t find this page|"
    r"эту страницу не удалось найти|объявление больше недоступно",
    re.I,
)
_REMOVED = re.compile(
    r"no longer available|has been removed|listing unavailable|"
    r"больше недоступно|снято с публикации",
    re.I,
)
_LOGIN = re.compile(r"/login|/signup|/account|log in|sign up|войти", re.I)
_GENERIC_AIRBNB_TITLE = re.compile(
    r"airbnb:\s*vacation rentals|airbnb \| vacation rentals",
    re.I,
)
_LISTING_MARKERS = re.compile(
    r"book it|check-in|checkout|reserve|show all photos|"
    r"where you.?ll sleep|what this place offers|"
    r"забронировать|заезд|выезд",
    re.I,
)


_ROOM_URL = re.compile(r"/rooms/(\d+)", re.I)


class PageKind(str, Enum):
    LISTING_PAGE = "LISTING_PAGE"
    GENERIC_HOMEPAGE = "GENERIC_HOMEPAGE"
    LOGIN_PAGE = "LOGIN_PAGE"
    CAPTCHA = "CAPTCHA"
    CHALLENGE = "CHALLENGE"
    NOT_FOUND = "NOT_FOUND"
    LISTING_REMOVED = "LISTING_REMOVED"
    LISTING_REDIRECT = "LISTING_REDIRECT"
    UNKNOWN = "UNKNOWN"


def detect_page_state(url: str, title: str, body_text: str) -> "CheckResult | None":
    from .models import CheckResult

    _CAPTCHA_TITLE = re.compile(
        r"access denied|robot|verify|captcha|unusual traffic|security check",
        re.I,
    )
    _CAPTCHA_BODY = re.compile(
        r"verify you.?re a human|unusual traffic|captcha|security check|automated access",
        re.I,
    )
    if _CAPTCHA_URL.search(url):
        if "challenge" in url.lower():
            return CheckResult.CHALLENGE
        return CheckResult.CAPTCHA
    if _CAPTCHA_TITLE.search(title):
        if "challenge" in title.lower():
            return CheckResult.CHALLENGE
        return CheckResult.CAPTCHA
    sample = body_text[:8000]
    if _CAPTCHA_BODY.search(sample):
        if "challenge" in sample.lower():
            return CheckResult.CHALLENGE
        return CheckResult.CAPTCHA
    return None


def page_kind_to_check_result(kind: PageKind) -> "CheckResult":
    from .models import CheckResult

    mapping = {
        PageKind.CAPTCHA: CheckResult.CAPTCHA,
        PageKind.CHALLENGE: CheckResult.CHALLENGE,
        PageKind.GENERIC_HOMEPAGE: CheckResult.GENERIC_HOMEPAGE,
        PageKind.NOT_FOUND: CheckResult.NOT_FOUND,
        PageKind.LISTING_REMOVED: CheckResult.LISTING_UNAVAILABLE,
        PageKind.LISTING_REDIRECT: CheckResult.LISTING_UNAVAILABLE,
        PageKind.LOGIN_PAGE: CheckResult.LISTING_UNAVAILABLE,
    }
    return mapping.get(kind, CheckResult.PARSE_ERROR)


def extract_listing_id(url: str) -> str | None:
    m = _ROOM_URL.search(url or "")
    return m.group(1) if m else None


def classify_page_kind(
    *,
    requested_url: str,
    final_url: str,
    title: str,
    body_text: str,
    expected_listing_id: str | None = None,
) -> PageKind:
    """Classify rendered page before calendar API matching."""
    from .models import CheckResult

    captcha_state = detect_page_state(final_url, title, body_text)
    if captcha_state == CheckResult.CHALLENGE:
        return PageKind.CHALLENGE
    if captcha_state == CheckResult.CAPTCHA:
        return PageKind.CAPTCHA

    url = (final_url or "").strip()
    title_l = (title or "").lower()
    body_sample = (body_text or "")[:12000]

    if _LOGIN.search(url) or _LOGIN.search(title_l):
        return PageKind.LOGIN_PAGE

    if _NOT_FOUND.search(title) or _NOT_FOUND.search(body_sample):
        return PageKind.NOT_FOUND

    if _REMOVED.search(body_sample):
        return PageKind.LISTING_REMOVED

    final_room = extract_listing_id(url)
    requested_room = expected_listing_id or extract_listing_id(requested_url)

    if final_room and _GENERIC_AIRBNB_TITLE.search(title):
        return PageKind.LISTING_REMOVED

    if _GENERIC_HOME.match(url.split("#")[0]):
        return PageKind.GENERIC_HOMEPAGE

    if requested_room and not final_room:
        return PageKind.LISTING_REDIRECT

    if final_room and requested_room and final_room != requested_room:
        return PageKind.LISTING_REDIRECT

    if final_room and (_LISTING_MARKERS.search(body_sample) or final_room == requested_room):
        return PageKind.LISTING_PAGE

    if final_room:
        return PageKind.LISTING_PAGE

    if requested_room:
        return PageKind.LISTING_REDIRECT

    return PageKind.UNKNOWN
