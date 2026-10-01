"""Facebook page URL classification and HTML cleanup for the Marketplace parser.

Share links (facebook.com/share/...) only become a listing when the logged-in
browser actually navigates to /marketplace/item/{id}. The Marketplace feed HTML
contains many other item links; those must not be treated as the share target.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

# XML 1.0 illegal controls: everything below U+0020 except TAB, LF, CR, plus DEL
# and the two Unicode noncharacters lxml rejects when building a tree.
# https://www.w3.org/TR/xml/#charsets
_XML_ILLEGAL_CHARS_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F\uFFFE\uFFFF]"
)
_DEC_CHAR_REF_RE = re.compile(r"&#(\d{1,7})\s*;")
_HEX_CHAR_REF_RE = re.compile(r"&#x([0-9a-fA-F]{1,6})\s*;")
_META_TAG_RE = re.compile(r"<(meta|link)\b([^>]*)>", re.I)
_ITEM_PATH_RE = re.compile(r"/marketplace/item/(\d+)")

AUTH_REQUIRED_PREFIX = "AUTH_REQUIRED:"


def sanitize_html(html: str | bytes | None) -> str:
    """Drop NULL bytes and other characters that make lxml refuse the document.

    crawl4ai parses fetched HTML with lxml (`document_fromstring`). Facebook
    responses sometimes contain literal NULL / C0 controls, and lxml then raises
    "All strings must be XML compatible: Unicode or ASCII, no NULL bytes or
    control characters". The scrape step logs that and returns an empty tree,
    so the rest of the run never sees the listing.
    """
    if html is None:
        return ""
    if isinstance(html, bytes):
        text = html.decode("utf-8", errors="replace")
    else:
        text = str(html)
    text = _DEC_CHAR_REF_RE.sub(_replace_dec_ref, text)
    text = _HEX_CHAR_REF_RE.sub(_replace_hex_ref, text)
    return _XML_ILLEGAL_CHARS_RE.sub("", text)


def _xml_char_allowed(codepoint: int) -> bool:
    return codepoint in (9, 10, 13) or codepoint >= 32


def _replace_dec_ref(match: re.Match[str]) -> str:
    try:
        codepoint = int(match.group(1))
    except ValueError:
        return match.group(0)
    if _xml_char_allowed(codepoint):
        return match.group(0)
    return ""


def _replace_hex_ref(match: re.Match[str]) -> str:
    try:
        codepoint = int(match.group(1), 16)
    except ValueError:
        return match.group(0)
    if _xml_char_allowed(codepoint):
        return match.group(0)
    return ""


_lxml_html_patched = False


def install_lxml_html_sanitizer() -> None:
    """Make lxml.html parse Facebook HTML that contains control characters.

    crawl4ai calls both `document_fromstring` (scrape) and `fromstring`
    (schema preprocess) on the raw document. Sanitizing at those entry points
    keeps a NULL byte from aborting HTML processing.
    """
    global _lxml_html_patched
    if _lxml_html_patched:
        return
    import lxml.html as lhtml

    original_fromstring = lhtml.fromstring
    original_document_fromstring = lhtml.document_fromstring

    def fromstring(html, *args, **kwargs):
        if isinstance(html, (str, bytes)):
            html = sanitize_html(html)
        return original_fromstring(html, *args, **kwargs)

    def document_fromstring(html, *args, **kwargs):
        if isinstance(html, (str, bytes)):
            html = sanitize_html(html)
        return original_document_fromstring(html, *args, **kwargs)

    lhtml.fromstring = fromstring
    lhtml.document_fromstring = document_fromstring
    _lxml_html_patched = True


def attach_html_sanitizer(strategy_cls):
    """Subclass a crawl4ai scraping strategy so lxml never sees raw FB HTML."""

    class SanitizedStrategy(strategy_cls):
        def scrap(self, url, html, **kwargs):
            return super().scrap(url, sanitize_html(html or ""), **kwargs)

    SanitizedStrategy.__name__ = "Sanitized" + strategy_cls.__name__
    return SanitizedStrategy


def public_facebook_url(url: str) -> str:
    """URL safe to print in logs: scheme, host, path. No query or fragment."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.split("?", 1)[0][:200]
    path = parsed.path or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"[:200]


def item_id_from_navigation_url(url: str) -> str | None:
    """Item id from the URL path only.

    Login redirects put the real item id in `?next=.../marketplace/item/123`.
    That is not a landing on the card. Feed HTML is not a navigation URL either;
    callers must not pass page HTML here.
    """
    if not url:
        return None
    parsed = urlparse(url.strip())
    path = unquote(parsed.path or "")
    match = _ITEM_PATH_RE.search(path)
    if not match:
        return None
    return match.group(1)


def navigation_kind(url: str) -> str:
    """Classify where the browser actually landed.

    Returns one of: item, login, feed, share, other.
    """
    raw = (url or "").strip()
    if not raw:
        return "other"
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host and "facebook.com" not in host:
        return "other"
    path = unquote(parsed.path or "").lower()
    if item_id_from_navigation_url(raw):
        return "item"
    if any(part in path for part in ("/login", "/checkpoint", "/recover", "/two_step")):
        return "login"
    if "/share/" in f"{path}/" or path.rstrip("/").endswith("/share"):
        return "share"
    trimmed = path.rstrip("/")
    if trimmed in ("", "/marketplace") or trimmed.startswith("/marketplace/"):
        return "feed"
    return "other"


def looks_like_login_wall(text: str) -> bool:
    lower = (text or "").lower()
    return (
        "log into facebook" in lower
        or ("увійти" in lower and "password" in lower)
        or ("email or mobile number" in lower and "forgot password" in lower)
    )


def page_is_login_wall(html: str) -> bool:
    """True when the document is a login wall rather than a listing payload.

    Logged-in Marketplace HTML often still contains the words "log in" inside
    scripts. A listing JSON field means the card payload is present.
    """
    text = html or ""
    if (
        "marketplace_listing_title" in text
        or "redacted_description" in text
        or '"listing_description"' in text
    ):
        return False
    sample = text if len(text) <= 200_000 else text[:200_000]
    return looks_like_login_wall(sample)


def html_has_listing_card(html: str, item_id: str) -> bool:
    if not html or not item_id or item_id not in html:
        return False
    return (
        "marketplace_listing_title" in html
        or "redacted_description" in html
        or '"listing_description"' in html
    )


def canonical_item_url(item_id: str) -> str:
    return f"https://www.facebook.com/marketplace/item/{item_id}/"


def canonical_from_share_meta(html: str) -> str | None:
    """Read a single og:url / canonical item URL from a share document.

    Only call this when the browser is still on /share/. A Marketplace feed
    page is full of other listings; its meta must not pick one of them.
    """
    if not html:
        return None
    found: list[str] = []
    for match in _META_TAG_RE.finditer(html):
        attrs = match.group(2)
        is_og = re.search(r"""(?:property|name)\s*=\s*["']og:url["']""", attrs, re.I)
        is_canonical = re.search(r"""rel\s*=\s*["']canonical["']""", attrs, re.I)
        if not is_og and not is_canonical:
            continue
        value_match = re.search(r"""(?:content|href)\s*=\s*["']([^"']+)["']""", attrs, re.I)
        if not value_match:
            continue
        raw = value_match.group(1).replace("\\/", "/")
        item_id = item_id_from_navigation_url(raw)
        if item_id:
            found.append(item_id)
    unique = list(dict.fromkeys(found))
    if len(unique) != 1:
        return None
    return canonical_item_url(unique[0])


def session_expired_message(detail: str) -> str:
    """Actionable AUTH_REQUIRED text. Exit code 2. No secrets."""
    detail = " ".join((detail or "").split())
    return (
        f"{AUTH_REQUIRED_PREFIX} Facebook session expired or not logged in. {detail} "
        "Refresh the login on the server (do not commit cookies, .env, or .fb_profile). "
        "1) sudo systemctl stop openhome-agent1 "
        "2) On a desktop: python agent1b/login_fb.py then python agent1b/export_fb_state.py "
        "3) On the VPS: python agent1b/import_fb_state.py "
        "4) cd /opt/openhome/app && git pull && sudo systemctl start openhome-agent1. "
        "Full steps: agent_1_parser/fb_parser/README.md "
        "(Refresh Facebook login on the server)."
    )


def resolve_share_target(final_url: str, html: str = "") -> str:
    """Map a share-link landing to the canonical item URL, or raise AUTH_REQUIRED.

    The item id is taken from the browser location path. Item links inside the
    feed HTML are ignored. A share document that never navigated may still name
    the listing in og:url / rel=canonical; that is the only HTML fallback.
    """
    html = sanitize_html(html or "")
    kind = navigation_kind(final_url)
    if kind == "item" and not page_is_login_wall(html):
        item_id = item_id_from_navigation_url(final_url)
        if item_id:
            return canonical_item_url(item_id)
    if kind == "share" and not page_is_login_wall(html):
        meta_url = canonical_from_share_meta(html)
        if meta_url:
            return meta_url
    if kind == "feed" or kind == "share":
        raise RuntimeError(
            session_expired_message(
                "Facebook opened the Marketplace feed instead of the item card "
                f"(landed on {public_facebook_url(final_url) or 'the share link'}). "
                "A saved c_user cookie still counts as logged out once the session expires."
            )
        )
    if kind == "login" or page_is_login_wall(html):
        raise RuntimeError(
            session_expired_message(
                "Facebook showed a login wall instead of the listing card "
                f"({public_facebook_url(final_url)})."
            )
        )
    raise RuntimeError(
        session_expired_message(
            "The share link did not open a Marketplace item "
            f"({public_facebook_url(final_url)})."
        )
    )


def assert_crawled_item_page(requested_url: str, final_url: str, html: str) -> str:
    """Confirm the crawl landed on the requested listing. Return its canonical URL."""
    html = sanitize_html(html or "")
    landed = (final_url or requested_url or "").strip()
    kind = navigation_kind(landed)
    if kind in {"feed", "share"}:
        raise RuntimeError(
            session_expired_message(
                "Facebook opened the Marketplace feed instead of the item card "
                f"(landed on {public_facebook_url(landed)})."
            )
        )
    if kind == "login" or page_is_login_wall(html):
        raise RuntimeError(
            session_expired_message(
                "Facebook showed a login wall instead of the listing card "
                f"({public_facebook_url(landed)})."
            )
        )
    if kind != "item":
        raise RuntimeError(
            "WRONG_PAGE: browser did not open a Marketplace item card "
            f"({public_facebook_url(landed)})."
        )
    final_id = item_id_from_navigation_url(landed)
    if not final_id:
        raise RuntimeError(
            "WRONG_PAGE: browser did not open a Marketplace item card "
            f"({public_facebook_url(landed)})."
        )
    requested_id = item_id_from_navigation_url(requested_url)
    if requested_id and requested_id != final_id:
        raise RuntimeError(
            session_expired_message(
                "Facebook left the requested listing and opened a different page "
                f"({public_facebook_url(landed)})."
            )
        )
    return canonical_item_url(final_id)


def share_wait_failed(probe) -> bool:
    """True when crawl4ai did not see /marketplace/item/{id} in the location path.

    crawl4ai 0.9 swallows a failed wait_for into CrawlResult.error_message
    instead of raising, and returns empty HTML.
    """
    if probe is None:
        return True
    error_message = str(getattr(probe, "error_message", "") or "")
    return "Wait condition failed" in error_message


def exit_code_for_parser_error(message: str) -> int:
    text = message or ""
    if text.startswith("AUTH_REQUIRED"):
        return 2
    if text.startswith("NO_PHOTOS"):
        return 3
    if text.startswith("WRONG_PAGE"):
        return 4
    return 1
