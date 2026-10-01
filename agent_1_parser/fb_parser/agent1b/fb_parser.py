#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load project .env so manual runs from any cwd get FB_* settings too.
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

from agent1b.fb_page import (
    assert_crawled_item_page,
    attach_html_sanitizer,
    canonical_item_url,
    exit_code_for_parser_error,
    html_has_listing_card,
    install_lxml_html_sanitizer,
    item_id_from_navigation_url,
    looks_like_login_wall,
    page_is_login_wall,
    public_facebook_url,
    resolve_share_target,
    sanitize_html,
    session_expired_message,
    share_wait_failed,
)
from agent1b.fb_post import (  # noqa: F401  (re-exported for tests and callers)
    POST_PAGE_JS,
    POST_SHARE_WAIT_JS,
    _looks_like_facebook_post_url,
    apply_post_listing,
    apply_swiped_gallery,
    canonicalize_facebook_post_url,
    extract_injected_post_gallery,
    extract_post_images_from_html,
    extract_post_text_from_html,
    facebook_pcb_photo_url,
    facebook_post_anchor,
    fallback_post_image_urls,
    is_post_share_url,
    merge_gallery_swipe,
    resolve_share_redirect,
)
from agent1b.fb_session import (
    build_browser_kwargs,
    ensure_facebook_session,
    get_profile_path,
    has_saved_session,
    human_delay,
    profile_lock,
)


@dataclass
class ListingData:
    source_url: str
    source_type: str = "facebook_marketplace"
    title: str = "Facebook Marketplace Listing"
    location_raw: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    price_raw: str = ""
    description: str = ""
    seller_name: str = ""
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    area_sqm: Optional[float] = None
    housing_type: str = ""
    amenities: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    parsed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    backend: str = ""
    debug: dict[str, Any] = field(default_factory=dict)


MARKETPLACE_JS_EXTRACT = r"""
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const pickText = (selectors) => {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el && el.innerText && el.innerText.trim()) return el.innerText.trim();
    }
    return "";
  };

  const sidebar = document.querySelector('[data-pagelet="MarketplaceSidebar"]') || document.body;

  // Expand "See more" / localized variants so full listing text is visible.
  const expandLabels = /see more|показать|ดูเพิ่ม|ver más|mehr anzeigen|voir plus|แสดงเพิ่ม/i;
  sidebar.querySelectorAll('[role="button"], span, div').forEach((btn) => {
    const t = (btn.innerText || "").trim();
    if (t && expandLabels.test(t) && t.length < 30) {
      try { btn.click(); } catch (e) {}
    }
  });
  await sleep(1200);

  const h1El =
    document.querySelector('[data-pagelet="MarketplaceSidebar"] h1') ||
    document.querySelector('[role="main"] h1') ||
    document.querySelector('h1');
  const title =
    (h1El && h1El.innerText && h1El.innerText.trim()) ||
    (document.querySelector('meta[property="og:title"]') || {}).content || "";

  // Price lives right under the h1 in the listing card. Climb from h1 so we
  // never pick prices from "similar items" cards elsewhere on the page.
  let price = "";
  const priceRe = /(?:฿|THB|baht|\$|€|£)\s*[\d][\d.,\s]*(?:\s*\/\s*[^\s,;]{1,15})?|[\d][\d.,\s]*\s*(?:฿|THB|baht)(?:\s*\/\s*[^\s,;]{1,15})?/i;
  if (h1El) {
    let node = h1El;
    for (let i = 0; i < 8 && node; i++) {
      node = node.parentElement;
      if (!node) break;
      const t = (node.innerText || "");
      const m = t.match(priceRe);
      if (m) { price = m[0].replace(/\s+/g, " ").trim(); break; }
    }
  }
  if (!price) {
    const priceNode = Array.from(sidebar.querySelectorAll('span, div'))
      .find((n) => priceRe.test((n.innerText || "").trim())
        && (n.innerText || "").trim().length < 40);
    if (priceNode) price = priceNode.innerText.trim();
  }

  const location =
    pickText([
      '[aria-label*="Location"] span',
      '[data-pagelet="MarketplaceSidebar"] a[href*="/marketplace/"] span'
    ]) || "";

  let description = "";
  const descCandidates = Array.from(
    sidebar.querySelectorAll(
      '[data-ad-comet-preview="message"], ' +
      '[data-testid="marketplace_pdp_description"], ' +
      'div[dir="auto"] span[dir="auto"]'
    )
  )
    .map((el) => (el.innerText || "").trim())
    .filter((t) => t.length > 80)
    .sort((a, b) => b.length - a.length);
  if (descCandidates.length) description = descCandidates[0];

  if (!description) {
    const ogDesc = (document.querySelector('meta[property="og:description"]') || {}).content || "";
    if (ogDesc.length > 40) description = ogDesc;
  }

  let seller = "";
  const sellerLink =
    document.querySelector('[role="main"] a[href*="/marketplace/profile/"]') ||
    sidebar.querySelector('a[href*="/marketplace/profile/"]');
  if (sellerLink) seller = (sellerLink.innerText || "").trim().split("\n")[0];

  const imgs = [];
  const pushImg = (u) => {
    if (!u) return;
    if (u.startsWith("data:")) return;
    if (!/^https?:/i.test(u)) return;
    // Tiny feed / profile / related thumbs (incl. 225×225 ad tiles).
    if (/emoji|static\.xx\.fbcdn|rsrc\.php|profile_pic|safe_image\.php/i.test(u)) return;
    if (/[sp]\d{2,3}x\d{2,3}|ctp=s\d+x\d+|stp=.*(?:p|s)\d+x\d+/i.test(u)) {
      const m = u.match(/[sp](\d{2,3})x(\d{2,3})/i);
      if (m && (parseInt(m[1], 10) < 400 || parseInt(m[2], 10) < 400)) return;
    }
    imgs.push(u);
  };

  const currentItemId = (location.pathname.match(/marketplace\/item\/(\d+)/) || [])[1] || "";

  const isOtherListingCard = (img) => {
    let el = img;
    for (let i = 0; i < 14 && el; i++) {
      if (el.tagName === "A") {
        const href = el.getAttribute("href") || "";
        const m = href.match(/marketplace\/item\/(\d+)/);
        if (m && currentItemId && m[1] !== currentItemId) return true;
      }
      const aria = (el.getAttribute && el.getAttribute("aria-label")) || "";
      if (/similar|related|more from|you may also|sponsored|suggested|похож|ещё от|แนะนำ|สินค้าที่คล้าย/i.test(aria)) {
        return true;
      }
      el = el.parentElement;
    }
    return false;
  };

  const inExcludedSection = (img) => {
    let el = img;
    for (let i = 0; i < 16 && el; i++) {
      const txt = ((el.innerText || "") + " " + ((el.getAttribute && el.getAttribute("aria-label")) || "")).slice(0, 240);
      if (/similar listings|related listings|more from this seller|you may also like|sponsored|suggested for you|похожие|ещё от продавца/i.test(txt)
          && (el.querySelectorAll && el.querySelectorAll("img").length >= 2)) {
        // Only treat as related rail when the node looks like a multi-card shelf,
        // not the main listing description which may mention those words.
        const links = el.querySelectorAll ? el.querySelectorAll('a[href*="/marketplace/item/"]') : [];
        if (links.length >= 2) return true;
      }
      el = el.parentElement;
    }
    return false;
  };

  const collectFromImg = (img) => {
    if (!img || isOtherListingCard(img) || inExcludedSection(img)) return;
    const w = img.naturalWidth || img.width || 0;
    const h = img.naturalHeight || img.height || 0;
    // Keep unknown sizes (lazy); drop clearly tiny on-page thumbs.
    if ((w > 0 && w < 350) || (h > 0 && h < 350)) return;
    pushImg(img.currentSrc || img.src);
    const srcset = img.getAttribute("srcset") || "";
    srcset.split(",").forEach((part) => {
      const u = part.trim().split(" ")[0];
      pushImg(u);
    });
  };

  // Prefer listing media viewer / left gallery — NOT every img under role=main
  // (that pulls similar-item cards and Marketplace promo creatives).
  const galleryRoots = [
    document.querySelector('[data-pagelet="MediaViewerPhoto"]'),
    document.querySelector('[data-pagelet="MarketplacePDPHero"]'),
    document.querySelector('[aria-label*="Gallery"]'),
    document.querySelector('[aria-label*="Photo"]'),
  ].filter(Boolean);

  let scoped = [];
  galleryRoots.forEach((root) => {
    root.querySelectorAll('img[src*="scontent"], img[src*="fbcdn"]').forEach((img) => scoped.push(img));
  });

  if (!scoped.length) {
    // Fallback: images in main that are NOT links to other listings.
    document.querySelectorAll(
      '[role="main"] img[src*="scontent"], [role="main"] img[src*="fbcdn"]'
    ).forEach((img) => scoped.push(img));
  }

  scoped.forEach(collectFromImg);

  // og:image belongs to this listing (usually first gallery photo).
  const og = document.querySelector('meta[property="og:image"]');
  if (og && og.content) pushImg(og.content);

  // Unique preserve order.
  const seen = new Set();
  const image_urls = [];
  for (const u of imgs) {
    if (seen.has(u)) continue;
    seen.add(u);
    image_urls.push(u);
  }

  return JSON.stringify({
    title,
    price,
    location,
    description,
    seller,
    image_urls
  });
})();
"""


# Mobile share links: facebook.com/share/{code}/ → redirects to marketplace/item/{id}
_SHARE_PATH_RE = re.compile(
    r"(?:(?:www|m|mbasic)\.)?facebook\.com/share/([A-Za-z0-9][A-Za-z0-9/_-]*)",
    re.I,
)


def is_share_url(url: str) -> bool:
    return bool(_SHARE_PATH_RE.search(url or ""))


def normalize_share_url(url: str) -> str:
    """Clean facebook.com/share/... to desktop www without tracking query."""
    match = _SHARE_PATH_RE.search(url or "")
    if not match:
        raise ValueError("URL does not look like Facebook share URL.")
    path = match.group(1).rstrip("/")
    return f"https://www.facebook.com/share/{path}/"


def normalize_marketplace_url(url: str) -> str:
    """Rewrite any Marketplace item host (www / m / mbasic) to desktop canonical URL.

    m.facebook.com often returns "Facebook is not available on this browser";
    parsing always goes through www.facebook.com/marketplace/item/{id}/.
    The id is read from the path only, so a login URL with the item in ?next=
    is not treated as the listing.
    """
    item_id = item_id_from_navigation_url(url)
    if not item_id:
        raise ValueError("URL does not look like Facebook Marketplace item URL.")
    return canonical_item_url(item_id)


def prepare_listing_url(url: str) -> str:
    """Accept Marketplace item, /share/ item link, or /share/p/ post; return crawl URL.

    HTTP 302 is followed when Facebook publishes one (item card or page post).
    /share/p/ that does not redirect stays a post URL for the post crawler.
    """
    if is_share_url(url):
        cleaned = normalize_share_url(url)
        return resolve_share_redirect(cleaned)
    return normalize_marketplace_url(url)


def extract_item_id(url: str) -> str:
    item_id = item_id_from_navigation_url(url)
    if not item_id:
        raise ValueError("Cannot extract Marketplace item id.")
    return item_id


def extract_first(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ""


def parse_bedrooms(text: str) -> Optional[int]:
    raw = extract_first(
        [
            r"\b(\d+)\s*(?:bedrooms?|beds?|спален|спальни|br)\b",
            r"\b(\d+)\s*beds?\b",
        ],
        text,
    )
    return int(raw) if raw else None


def parse_bathrooms(text: str) -> Optional[int]:
    raw = extract_first(
        [r"\b(\d+)\s*(?:bathrooms?|baths?|санузла|санузлов|ванн)\b"],
        text,
    )
    return int(raw) if raw else None


def parse_area_sqm(text: str) -> Optional[float]:
    sqm = extract_first(
        [
            r"(?:built-up area|area|площадь)\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(?:m²|sqm|sq\.?\s?m|м²)",
            r"(\d+(?:[.,]\d+)?)\s*(?:m²|sqm|sq\.?\s?m|м²)",
        ],
        text,
    )
    if sqm:
        return float(sqm.replace(",", "."))
    sqft = extract_first([r"(\d+(?:[.,]\d+)?)\s*(?:sq\.?\s?ft|ft²|sqft)"], text)
    if sqft:
        return round(float(sqft.replace(",", ".")) * 0.092903, 1)
    return None


def parse_housing_type(text: str) -> str:
    lower = text.lower()
    mapping = [
        ("villa", "вилла"),
        ("condo", "кондо"),
        ("apartment", "квартира"),
        ("house", "дом"),
        ("townhouse", "таунхаус"),
        ("вилла", "вилла"),
        ("кондо", "кондо"),
        ("дом", "дом"),
        ("квартира", "квартира"),
    ]
    for needle, label in mapping:
        if needle in lower:
            return label
    return ""


def is_listing_page_content(text: str, item_id: str) -> bool:
    """Reject feed/browse pages that stole unrelated thumbnails."""
    lower = (text or "").lower()
    if not text or len(text.strip()) < 40:
        return False
    if looks_like_login_wall(text):
        return False
    # Classic browse/feed noise from previous broken parses.
    browse_markers = [
        "вибір дня",
        "категорії",
        "создать объявление",
        "створити оголошення",
        "top picks",
        "today's picks",
    ]
    if sum(1 for m in browse_markers if m in lower) >= 2 and item_id not in text:
        return False
    # Must look like a concrete listing card.
    signals = 0
    if re.search(r"\d[\d\s.,]{2,}\s*(?:฿|thb|baht)|฿\s*\d", lower):
        signals += 1
    if re.search(r"\b(?:bed|beds|bedroom|спальн|bath|санузл|sqm|m²|вилла|villa|house|дом)\b", lower):
        signals += 1
    if item_id in text:
        signals += 1
    if "marketplace/item/" in lower:
        signals += 1
    return signals >= 2


def unescape_fb_url(value: str) -> str:
    value = value.replace("\\/", "/")
    value = value.encode("utf-8").decode("unicode_escape", errors="ignore")
    return unquote(value)


def is_junk_image_url(url: str) -> bool:
    """Drop feed thumbs, profile pics, and tiny related/ad tiles by URL hints."""
    lower = (url or "").lower()
    junk_substrings = [
        "emoji",
        "static.xx.fbcdn",
        "rsrc.php",
        "profile_pic",
        "safe_image.php",
    ]
    if any(j in lower for j in junk_substrings):
        return True
    # Facebook CDN size tokens: s60x60, p225x225, ctp=s261x260, etc.
    for m in re.finditer(r"(?:[sp]|ctp=s|stp=[^&]*[sp])(\d{2,4})x(\d{2,4})", lower):
        try:
            w, h = int(m.group(1)), int(m.group(2))
        except ValueError:
            continue
        if w < 400 or h < 400:
            return True
    return False


def _image_dimensions(content: bytes) -> Optional[tuple[int, int]]:
    """Best-effort JPEG/PNG dimensions from raw bytes (no Pillow required)."""
    if not content or len(content) < 24:
        return None
    # PNG
    if content[:8] == b"\x89PNG\r\n\x1a\n" and len(content) >= 24:
        w = int.from_bytes(content[16:20], "big")
        h = int.from_bytes(content[20:24], "big")
        if w > 0 and h > 0:
            return w, h
    # JPEG SOF0/SOF2 scan
    if content[:2] == b"\xff\xd8":
        i = 2
        n = len(content)
        while i + 9 < n:
            if content[i] != 0xFF:
                i += 1
                continue
            marker = content[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):  # SOF
                h = int.from_bytes(content[i + 5 : i + 7], "big")
                w = int.from_bytes(content[i + 7 : i + 9], "big")
                if w > 0 and h > 0:
                    return w, h
                return None
            if marker == 0xD9 or marker == 0xDA:
                break
            if marker == 0x01 or (0xD0 <= marker <= 0xD9):
                i += 2
                continue
            seg_len = int.from_bytes(content[i + 2 : i + 4], "big")
            if seg_len < 2:
                break
            i += 2 + seg_len
    return None


def is_junk_image_bytes(content: bytes, *, min_side: int = 400) -> bool:
    """Reject tiny related/ad tiles after download (e.g. 225×225 promo thumbs)."""
    if not content or len(content) < 20_000:
        return True
    dims = _image_dimensions(content)
    if not dims:
        return False  # keep if we cannot parse — URL filters already applied
    w, h = dims
    if w < min_side or h < min_side:
        return True
    return False


def filter_minority_square_promos(
    saved_rel_paths: list[str],
    photos_dir: Path,
) -> list[str]:
    """Drop minority exact-square creatives among mostly non-square listing photos.

    Marketplace pages often inject 1:1 ad banners (NEXT/Radisson-style) into the
    scraped set while real villa photos are 4:3 / 3:4. When squares are a small
    minority, drop them.
    """
    if len(saved_rel_paths) < 5:
        return saved_rel_paths

    dims: list[tuple[str, int, int]] = []
    for rel in saved_rel_paths:
        path = photos_dir / Path(rel).name
        if not path.exists():
            continue
        try:
            d = _image_dimensions(path.read_bytes())
        except OSError:
            d = None
        if d:
            dims.append((rel, d[0], d[1]))

    if len(dims) < 5:
        return saved_rel_paths

    def is_square(w: int, h: int) -> bool:
        return 0.92 <= (w / h) <= 1.08

    squares = [rel for rel, w, h in dims if is_square(w, h)]
    nonsquare_n = len(dims) - len(squares)
    if nonsquare_n >= 5 and len(squares) <= 3 and (len(squares) / len(dims)) <= 0.25:
        drop = set(squares)
        # Also remove the dropped files from disk so Agent2 does not re-upload them.
        for rel in drop:
            try:
                (photos_dir / Path(rel).name).unlink(missing_ok=True)
            except OSError:
                pass
        kept = [rel for rel in saved_rel_paths if rel not in drop]
        # Re-number not required — Agent2 uses the file list as-is.
        return kept
    return saved_rel_paths


def extract_listing_images_from_html(html: str, item_id: str) -> list[str]:
    if not html:
        return []

    urls: list[str] = []

    # Prefer media arrays close to this item id.
    for m in re.finditer(re.escape(item_id), html):
        start = max(0, m.start() - 80000)
        end = min(len(html), m.end() + 80000)
        chunk = html[start:end]
        for pattern in (
            r'"(?:uri|url|image_url|src)":"(https:\\/\\/scontent[^"]+)"',
            r'"(?:uri|url|image_url|src)":"(https:\\/\\/[^"]*fbcdn[^"]+)"',
            r'"(?:uri|url)":"(https://scontent[^"]+)"',
        ):
            for u in re.findall(pattern, chunk):
                urls.append(unescape_fb_url(u))

    og = re.findall(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )
    urls.extend(og)

    filtered = []
    for u in dedupe_urls(urls):
        if is_junk_image_url(u):
            continue
        if "fbcdn" in u.lower() or any(ext in u.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            filtered.append(u)
    return filtered


def parse_js_payload(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}

    def unwrap(obj: Any) -> dict[str, Any]:
        if isinstance(obj, dict):
            if "results" in obj and isinstance(obj["results"], list) and obj["results"]:
                first = obj["results"][0]
                if isinstance(first, dict):
                    if "result" in first:
                        return unwrap(first["result"])
                    if "value" in first:
                        return unwrap(first["value"])
                    if "data" in first:
                        return unwrap(first["data"])
                return unwrap(first)
            if "result" in obj:
                return unwrap(obj["result"])
            if "value" in obj:
                return unwrap(obj["value"])
            if "data" in obj:
                return unwrap(obj["data"])
            if any(k in obj for k in ("title", "description", "price", "image_urls")):
                return obj
            return obj
        if isinstance(obj, list) and obj:
            return unwrap(obj[0])
        if isinstance(obj, str):
            text = obj.strip()
            if not text:
                return {}
            try:
                return unwrap(json.loads(text))
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    try:
                        return unwrap(json.loads(m.group(0)))
                    except json.JSONDecodeError:
                        return {}
        return {}

    if isinstance(raw, dict):
        return unwrap(raw)
    if isinstance(raw, list) and raw:
        return unwrap(raw[0])
    if isinstance(raw, str):
        return unwrap(raw)
    return {}


def is_marketplace_noise(text: str) -> bool:
    if not text or not text.strip():
        return True
    lower = text.lower()
    noise_markers = [
        "marketplace/category",
        "category_menu_item",
        "referral_ui_component",
        "вибір дня",
        "top picks",
        "today's picks",
        "หมวดหมู่",
        "categories",
        "เมืองใกล้เคียง",
        "nearby cities",
        "создать объявление",
        "створити оголошення",
        "![รูปภาพ",
        "![](https://",
        "[เข้าสู่ระบบ]",
        "log into facebook",
    ]
    hits = sum(1 for m in noise_markers if m in lower)
    if hits >= 2:
        return True
    if text.count("http") > 8 or text.count("facebook.com/marketplace/") > 4:
        return True
    return False


def _decode_json_string(value: str) -> str:
    value = value.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\/", "/")
    value = re.sub(r"\\u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), value)
    return value.strip()


def extract_listing_text_from_html(html: str, item_id: str) -> dict[str, str]:
    """Extract listing fields ONLY from JSON chunks near this item_id.

    Searching the whole page grabs related-listing data (e.g. wrong title/price
    from a 'similar items' card), so every pattern is matched inside windows
    around occurrences of the target item id.
    """
    out: dict[str, str] = {}
    html = sanitize_html(html)
    if not html:
        return out

    windows: list[str] = []
    for m in re.finditer(re.escape(item_id), html):
        start = max(0, m.start() - 20000)
        end = min(len(html), m.end() + 20000)
        windows.append(html[start:end])

    patterns = {
        "title": [
            r'"marketplace_listing_title":"([^"]{5,200})"',
            r'"custom_title":"([^"]{5,200})"',
            r'"base_marketplace_listing_title":"([^"]{5,200})"',
        ],
        "description": [
            r'"redacted_description":\{"text":"((?:\\.|[^"\\]){40,8000})"',
            r'"listing_description":\{"text":"((?:\\.|[^"\\]){40,8000})"',
            r'"description":\{"text":"((?:\\.|[^"\\]){40,8000})"',
        ],
        "price": [
            r'"formatted_price":\{"text":"([^"]{2,80})"',
            r'"formatted_price":"([^"]{2,80})"',
            r'"listing_price":\{"amount":"([^"]+)"',
        ],
        "location": [
            r'"reverse_geocode":\{"city":"([^"]+)"',
            r'"reverse_geocode_detailed":\{"city":"([^"]+)"',
            r'"location_text":\{"text":"([^"]{3,120})"',
            r'"location_text":"([^"]{3,120})"',
        ],
        "seller": [
            r'"marketplace_listing_seller":\{[^{}]{0,200}?"name":"([^"]{2,120})"',
            r'"actors":\[\{[^\[\]]{0,300}?"name":"([^"]{2,120})"',
            r'"seller":\{[^{}]{0,200}?"name":"([^"]{2,120})"',
        ],
    }

    for field, field_patterns in patterns.items():
        for pattern in field_patterns:
            found = ""
            for chunk in windows:
                m = re.search(pattern, chunk)
                if m:
                    found = _decode_json_string(m.group(1))
                    if found:
                        break
            if found:
                out[field] = found
                break

    # Map pin coordinates for the listing (FB shows an approximate location map).
    coord_patterns = [
        r'"location":\{"latitude":(-?\d{1,3}\.\d+),"longitude":(-?\d{1,3}\.\d+)',
        r'"latitude":(-?\d{1,3}\.\d+),"longitude":(-?\d{1,3}\.\d+)',
        r'"pin_location":\{[^{}]*?"latitude":(-?\d{1,3}\.\d+)[^{}]*?"longitude":(-?\d{1,3}\.\d+)',
    ]
    for pattern in coord_patterns:
        found_coords = None
        for chunk in windows:
            m = re.search(pattern, chunk)
            if m:
                lat, lng = float(m.group(1)), float(m.group(2))
                if -90 <= lat <= 90 and -180 <= lng <= 180 and (lat, lng) != (0.0, 0.0):
                    found_coords = (lat, lng)
                    break
        if found_coords:
            out["latitude"] = str(found_coords[0])
            out["longitude"] = str(found_coords[1])
            break

    # Fallback: the static map image URL carries the pin as center=lat,lng.
    if "latitude" not in out:
        m = re.search(
            r'static_map\.php[^"\']{0,600}?center=(-?\d{1,3}\.\d+)(?:%2C|,|\\u00252C)(-?\d{1,3}\.\d+)',
            html,
        )
        if m:
            out["latitude"] = m.group(1)
            out["longitude"] = m.group(2)

    # og: meta belongs to the opened listing — safe page-wide fallback.
    og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    og_desc = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
    if og_title and "title" not in out:
        out["title"] = og_title.group(1).strip()
    if og_desc and "description" not in out:
        out["description"] = og_desc.group(1).strip()

    return out


def pick_listing_description(*candidates: str) -> str:
    for text in candidates:
        t = (text or "").strip()
        if not t:
            continue
        if is_marketplace_noise(t):
            continue
        return sanitize_text(t[:8000])
    return ""


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    # Remove lone surrogates that break utf-8 file writes.
    cleaned = "".join(ch for ch in text if not (0xD800 <= ord(ch) <= 0xDFFF))
    return cleaned.encode("utf-8", errors="replace").decode("utf-8")


def safe_write_text(path: Path, text: str) -> None:
    path.write_text(sanitize_text(text), encoding="utf-8", errors="replace")


def enrich_from_text(listing: ListingData, text: str) -> None:
    if not listing.bedrooms:
        listing.bedrooms = parse_bedrooms(text)
    if not listing.bathrooms:
        listing.bathrooms = parse_bathrooms(text)
    if not listing.area_sqm:
        listing.area_sqm = parse_area_sqm(text)
    if not listing.housing_type:
        listing.housing_type = parse_housing_type(text)
    if not listing.price_raw:
        listing.price_raw = extract_first(
            [
                r"((?:\d[\d\s.,]{2,})\s*(?:฿|THB|baht)(?:\s*/\s*(?:месяц|month))?)",
                r"((?:฿|\$)\s*\d[\d\s.,]{2,}(?:\s*/\s*(?:месяц|month))?)",
            ],
            text,
        )
    if not listing.location_raw:
        listing.location_raw = extract_first(
            [
                r"(Choeng Thale|Bangtao|Bangtáo|Patong|Rawai|Kamala|Kathu|Phuket|Пхукет)[^\n,]{0,40}",
            ],
            text,
        )


def make_sanitized_scraping_strategy():
    """crawl4ai scraping strategy that strips XML-illegal characters before lxml."""
    from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy

    install_lxml_html_sanitizer()
    return attach_html_sanitizer(LXMLWebScrapingStrategy)()


def _crawler_run_config(*, strategy, share: bool):
    from crawl4ai import CacheMode, CrawlerRunConfig

    page_delay = float(os.getenv("FB_PAGE_DELAY_SEC", "4"))
    if share:
        # Client-side redirect from /share/{code}/ to /marketplace/item/{id}/.
        page_delay = max(page_delay, 6.0)
    run_kwargs: dict[str, Any] = {
        "cache_mode": CacheMode.BYPASS,
        "wait_until": "domcontentloaded",
        "page_timeout": 90000,
        "delay_before_return_html": page_delay,
        "js_code": [MARKETPLACE_JS_EXTRACT],
        "scraping_strategy": strategy,
    }
    if share:
        # Path only: a login URL with the item id in ?next= must not count.
        run_kwargs["wait_for"] = (
            "js:() => /\\/marketplace\\/item\\/\\d+/.test(window.location.pathname)"
        )
        run_kwargs["wait_for_timeout"] = 35000
    return CrawlerRunConfig(**run_kwargs)


def _post_page_config(strategy, *, wait: bool):
    from crawl4ai import CacheMode, CrawlerRunConfig

    page_delay = max(float(os.getenv("FB_PAGE_DELAY_SEC", "4")), 8.0)
    run_kwargs: dict[str, Any] = {
        "cache_mode": CacheMode.BYPASS,
        "wait_until": "domcontentloaded",
        "page_timeout": 120000,
        "delay_before_return_html": page_delay,
        "js_code": [POST_PAGE_JS],
        "scraping_strategy": strategy,
    }
    if wait:
        run_kwargs["wait_for"] = POST_SHARE_WAIT_JS
        run_kwargs["wait_for_timeout"] = 25000
    return CrawlerRunConfig(**run_kwargs)


async def _fetch_post_page(crawler, url: str, strategy):
    """Open a post share. A failed wait still returns the rendered HTML."""
    try:
        probe = await crawler.arun(url=url, config=_post_page_config(strategy, wait=True))
    except RuntimeError as exc:
        if "Wait condition failed" not in str(exc):
            raise
        probe = None
    if share_wait_failed(probe) or not _crawl_html(probe):
        probe = await crawler.arun(url=url, config=_post_page_config(strategy, wait=False))
    return probe


def _crawl_html(result) -> str:
    return sanitize_html(getattr(result, "html", "") or "")


def _result_markdown(result) -> str:
    if not hasattr(result, "markdown"):
        return ""
    markdown_obj = result.markdown
    if isinstance(markdown_obj, str):
        return markdown_obj
    if hasattr(markdown_obj, "raw_markdown"):
        return markdown_obj.raw_markdown or ""
    return ""


async def _resolve_share_in_crawler(crawler, share_url: str, strategy) -> tuple[str, Any]:
    """Follow a share link in the logged-in browser and return (canonical_url, result_or_none).

    When the browser lands on the item card, `result` is that page and the caller
    can parse it directly. When the share document only names the item (og:url)
    or the card did not render, `result` is None and the caller must open the
    canonical /marketplace/item/{id}/ URL.
    """
    try:
        probe = await crawler.arun(
            url=share_url,
            config=_crawler_run_config(strategy=strategy, share=True),
        )
    except RuntimeError as exc:
        if "Wait condition failed" not in str(exc):
            raise
        probe = None
    if share_wait_failed(probe):
        # Redirect never reached /marketplace/item/{id}/ in the location path.
        # Load the page anyway so og:url can still name the listing, or so a
        # feed/login landing becomes a session-expired error instead of a timeout.
        probe = await crawler.arun(
            url=share_url,
            config=_crawler_run_config(strategy=strategy, share=False),
        )
    final_url = getattr(probe, "redirected_url", None) or getattr(probe, "url", None) or ""
    html = _crawl_html(probe)
    canonical = resolve_share_target(final_url, html)
    item_id = item_id_from_navigation_url(canonical)
    if item_id_from_navigation_url(final_url) == item_id and html_has_listing_card(html, item_id):
        return canonical, probe
    return canonical, None


async def crawl_with_crawl4ai(url: str) -> ListingData:
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    share = is_share_url(url)
    post_mode = is_post_share_url(url) or _looks_like_facebook_post_url(url)
    listing = ListingData(source_url=url, backend="crawl4ai")
    listing.debug["share_url"] = share
    listing.debug["post_share"] = post_mode

    profile_path = get_profile_path()
    if not has_saved_session(profile_path):
        raise RuntimeError(
            session_expired_message(
                "No saved Facebook session (c_user cookie missing)."
            )
        )

    await human_delay()

    browser_kwargs = build_browser_kwargs(for_login=False)
    listing.debug["headless"] = browser_kwargs.get("headless")
    listing.debug["profile_dir"] = browser_kwargs.get("user_data_dir")
    listing.debug["proxy"] = bool(browser_kwargs.get("proxy_config"))

    browser_config = BrowserConfig(**browser_kwargs)
    strategy = make_sanitized_scraping_strategy()

    async with profile_lock():
        await human_delay()
        async with AsyncWebCrawler(config=browser_config) as crawler:
            if post_mode:
                result = await _fetch_post_page(crawler, url, strategy)
                prefetched = result
            elif share:
                url, prefetched = await _resolve_share_in_crawler(crawler, url, strategy)
                listing.debug["resolved_item_url"] = url
            else:
                prefetched = None
            if prefetched is not None:
                result = prefetched
            else:
                result = await crawler.arun(
                    url=url,
                    config=_crawler_run_config(strategy=strategy, share=False),
                )

    html = _crawl_html(result)
    error_message = str(getattr(result, "error_message", "") or "")
    listing.debug["crawl_error"] = error_message[:500]
    redirected = getattr(result, "redirected_url", None) or ""
    result_url = getattr(result, "url", None) or ""
    listing.debug["redirected_url"] = (redirected or result_url)[:300]

    if "anti-bot" in error_message.lower() or ("<body" not in html.lower() and len(html) < 20000):
        raise RuntimeError(
            session_expired_message(
                "Facebook blocked the crawler (anti-bot) or returned an empty page."
            )
        )
    markdown = _result_markdown(result)

    js_payload = parse_js_payload(getattr(result, "js_execution_result", None))
    if not js_payload:
        # Some crawl4ai versions put console / extracted content here.
        js_payload = parse_js_payload(getattr(result, "extracted_content", None))

    landed = redirected or result_url or url
    if post_mode or is_post_share_url(landed) or _looks_like_facebook_post_url(landed):
        if page_is_login_wall(html):
            raise RuntimeError(
                session_expired_message(
                    "Facebook showed a login wall instead of the post "
                    f"({public_facebook_url(landed)})."
                )
            )
        filled = apply_post_listing(
            listing,
            html=html,
            js_payload=js_payload,
            result=result,
            fallback_url=landed or url,
        )
        # Swipe is best when the post has a viewer. Zero or one frame keeps
        # attachment / og:image / <img> URLs already on the listing.
        await merge_gallery_swipe(
            filled,
            filled.source_url or landed or url,
            prefer_swipe=True,
        )
        if not filled.image_urls:
            raise RuntimeError(
                "NO_PHOTOS: Facebook post opened, but gallery swipe found no photos."
            )
        return filled

    # Item id comes from the browser location, never from feed-card links in HTML.
    listing.source_url = assert_crawled_item_page(url, landed, html)
    item_id = extract_item_id(listing.source_url)

    if (
        not html_has_listing_card(html, item_id)
        and not (js_payload.get("title") or "").strip()
        and not is_listing_page_content(html[:150000], item_id)
    ):
        raise RuntimeError(
            session_expired_message(
                "The item URL opened a Marketplace feed shell instead of the listing card."
            )
        )

    listing.debug["crawl4ai_success"] = getattr(result, "success", None)
    listing.debug["js_keys"] = sorted(list(js_payload.keys()))

    html_fields = extract_listing_text_from_html(html, item_id)

    listing.title = (
        (js_payload.get("title") or "").strip()
        or html_fields.get("title", "").strip()
        or listing.title
    )
    if listing.title.lower() in {"facebook", "marketplace", "facebook marketplace", "facebook marketplace listing"}:
        m = re.search(r"(\d+\s*(?:beds?|bedrooms?|baths?|bathrooms?).{0,40}(?:house|villa|condo|apartment))", markdown, re.I)
        if m:
            listing.title = m.group(1).strip()

    listing.price_raw = (js_payload.get("price") or html_fields.get("price") or "").strip()
    listing.location_raw = (js_payload.get("location") or html_fields.get("location") or "").strip()
    listing.seller_name = (js_payload.get("seller") or html_fields.get("seller") or "").strip()
    if html_fields.get("latitude") and html_fields.get("longitude"):
        listing.latitude = float(html_fields["latitude"])
        listing.longitude = float(html_fields["longitude"])

    listing.description = pick_listing_description(
        js_payload.get("description"),
        html_fields.get("description"),
    )
    listing.debug["description_source"] = (
        "js" if js_payload.get("description") and not is_marketplace_noise(str(js_payload.get("description")))
        else "html_json" if html_fields.get("description")
        else "none"
    )

    enrich_from_text(
        listing,
        "\n".join([listing.title, listing.price_raw, listing.description, html_fields.get("description", "")]),
    )

    image_urls: list[str] = []
    for u in js_payload.get("image_urls") or []:
        if isinstance(u, str) and not is_junk_image_url(u):
            image_urls.append(u)

    if not image_urls:
        image_urls = extract_listing_images_from_html(html, item_id)

    # Last resort: crawl4ai media, but drop feed thumbnails.
    if not image_urls:
        media = getattr(result, "media", None)
        if isinstance(media, dict) and "images" in media:
            for img in media["images"] or []:
                u = ""
                if isinstance(img, dict):
                    u = img.get("src") or img.get("url") or ""
                elif isinstance(img, str):
                    u = img
                if u and not is_junk_image_url(u):
                    image_urls.append(u)
        listing.debug["image_strategy"] = "crawl4ai_media_filtered"
    else:
        listing.debug["image_strategy"] = "js_or_item_html"

    listing.image_urls = dedupe_urls(image_urls)
    listing.debug["image_count_detected"] = len(listing.image_urls)

    if not listing.image_urls:
        raise RuntimeError("NO_PHOTOS: listing card opened, but no listing gallery URLs found.")

    # Hard reject tiny/feed thumbs if somehow still mixed in majority.
    big = [u for u in listing.image_urls if not is_junk_image_url(u)]
    listing.image_urls = big or listing.image_urls
    return listing


def crawl_with_scrapegraph(url: str) -> ListingData:
    from scrapegraphai.graphs import SmartScraperGraph

    prompt = (
        "You are extracting ONE current Facebook Marketplace listing card only. "
        "Ignore related/similar listings and Marketplace feed. Return JSON with keys: "
        "title, price_raw, location_raw, description, seller_name, bedrooms, bathrooms, area_sqm, "
        "housing_type, amenities (array), image_urls (array of large gallery image URLs only). "
        "Prefer images from the current item gallery, not 261x260 thumbnails, "
        "not similar/related cards, not promotional/ad creatives with CTA buttons."
    )
    api_key = os.getenv("OPENAI_API_KEY", "").strip() or "YOUR_OPENAI_API_KEY"
    config = {
        "llm": {"model": "openai/gpt-4o-mini", "api_key": api_key},
        "verbose": False,
        "headless": True,
    }
    graph = SmartScraperGraph(prompt=prompt, source=url, config=config)
    result = graph.run()

    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {"description": result}

    text_for_guard = json.dumps(result, ensure_ascii=False)
    if looks_like_login_wall(text_for_guard):
        raise RuntimeError("AUTH_REQUIRED: Facebook returned login wall.")

    listing = ListingData(source_url=url, backend="scrapegraphai")
    listing.title = str(result.get("title") or listing.title)
    listing.price_raw = str(result.get("price_raw") or "")
    listing.location_raw = str(result.get("location_raw") or "")
    listing.description = str(result.get("description") or "")
    listing.seller_name = str(result.get("seller_name") or "")
    listing.housing_type = str(result.get("housing_type") or "")
    listing.amenities = [str(x) for x in (result.get("amenities") or [])][:30]
    listing.image_urls = [
        u for u in dedupe_urls([str(x) for x in (result.get("image_urls") or [])]) if not is_junk_image_url(u)
    ]
    listing.debug["raw_result"] = result

    try:
        listing.bedrooms = int(result["bedrooms"]) if result.get("bedrooms") is not None else None
    except (ValueError, TypeError):
        listing.bedrooms = None
    try:
        listing.bathrooms = int(result["bathrooms"]) if result.get("bathrooms") is not None else None
    except (ValueError, TypeError):
        listing.bathrooms = None
    try:
        listing.area_sqm = float(result["area_sqm"]) if result.get("area_sqm") is not None else None
    except (ValueError, TypeError):
        listing.area_sqm = None

    enrich_from_text(listing, listing.description + "\n" + listing.title)
    return listing


def dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if not url or url in seen:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        seen.add(url)
        out.append(url)
    return out


def save_images(image_urls: list[str], photos_dir: Path, timeout: int = 20) -> list[str]:
    photos_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    idx = 1
    for image_url in image_urls:
        if is_junk_image_url(image_url):
            continue
        suffix = ".jpg"
        parsed = urlparse(image_url)
        path_l = parsed.path.lower()
        if path_l.endswith(".png"):
            suffix = ".png"
        elif path_l.endswith(".webp"):
            suffix = ".webp"
        filename = f"photo_{idx:03d}{suffix}"
        target = photos_dir / filename
        try:
            with requests.get(
                image_url,
                timeout=timeout,
                stream=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                resp.raise_for_status()
                content = b"".join(chunk for chunk in resp.iter_content(chunk_size=1024 * 64) if chunk)
            # Drop tiny feed thumbs / ad tiles that slipped through URL filters.
            if is_junk_image_bytes(content):
                continue
            # Prefer real image extension from magic bytes.
            if content[:2] == b"\xff\xd8":
                suffix = ".jpg"
                filename = f"photo_{idx:03d}{suffix}"
                target = photos_dir / filename
            elif content[:8] == b"\x89PNG\r\n\x1a\n":
                suffix = ".png"
                filename = f"photo_{idx:03d}{suffix}"
                target = photos_dir / filename
            target.write_bytes(content)
            saved.append(f"photos/{filename}")
            idx += 1
        except Exception:
            continue
    return filter_minority_square_promos(saved, photos_dir)


def build_description(listing: ListingData) -> str:
    lines: list[str] = []
    lines.append(listing.title.strip() or "Facebook Marketplace Listing")
    lines.append("")

    location = listing.location_raw.strip() or "Thailand"
    if "thailand" not in location.lower() and "тайланд" not in location.lower():
        location = f"{location}, Thailand"
    lines.append(location)
    if listing.latitude is not None and listing.longitude is not None:
        lines.append(
            f"Координаты (приблизительно): {listing.latitude}, {listing.longitude}"
        )
        lines.append(f"Google Maps: https://www.google.com/maps?q={listing.latitude},{listing.longitude}")
    lines.append("")
    lines.append(listing.source_url)
    lines.append("")

    desc = (listing.description or "").strip()
    if desc and not is_marketplace_noise(desc):
        lines.append(desc)
        lines.append("")

    lines.append("Характеристики:")
    if listing.bedrooms is not None:
        lines.append(f"• {listing.bedrooms} спален / {listing.bedrooms} BR")
    if listing.bathrooms is not None:
        lines.append(f"• {listing.bathrooms} санузла")
    if listing.area_sqm is not None:
        lines.append(f"• {listing.area_sqm} m²")
    if listing.housing_type:
        lines.append(f"• Тип: {listing.housing_type}")
    lines.append("")

    if listing.price_raw:
        # Normalize toward Agent2-friendly format when possible.
        price = listing.price_raw
        if "thb" not in price.lower() and "฿" not in price and "baht" not in price.lower():
            price = f"{price} THB"
        if "месяц" not in price.lower() and "month" not in price.lower() and "/mois" not in price.lower():
            price = f"{price}/месяц"
        lines.append(f"Цена: {price}")
    else:
        lines.append("Цена: не указана")
    lines.append("Тип аренды: долгосрочная")
    lines.append("")

    if listing.amenities:
        lines.append("Удобства: " + ", ".join(listing.amenities))
        lines.append("")
    if listing.seller_name:
        lines.append(f"Контакт продавца: {listing.seller_name}")
        lines.append("")

    return sanitize_text("\n".join(lines).strip() + "\n")


def create_session_folder(base_dir: Path, session_id: str) -> Path:
    session_dir = base_dir / "data" / "sessions" / session_id
    (session_dir / "photos").mkdir(parents=True, exist_ok=True)
    return session_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agent 1B parser for Facebook Marketplace using crawl4ai or ScrapeGraphAI."
    )
    parser.add_argument("--url", required=True, help="Marketplace item or /share/ URL.")
    parser.add_argument("--session", required=True, help="Session ID for Agent 2 handoff.")
    parser.add_argument(
        "--backend",
        default="crawl4ai",
        choices=["crawl4ai", "scrapegraphai"],
        help="Extraction backend to use.",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root where data/sessions will be created.",
    )
    parser.add_argument(
        "--save-debug-json",
        action="store_true",
        help="Save parsed.json for debug (Agent 2 ignores it).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        crawl_url = prepare_listing_url(args.url)
    except ValueError as e:
        print(f"PARSER_FAILED: {e}", file=sys.stderr)
        return 1
    workspace = Path(args.workspace).resolve()
    session_dir = create_session_folder(workspace, args.session)

    try:
        if args.backend == "crawl4ai":
            listing = asyncio.run(crawl_with_crawl4ai(crawl_url))
        else:
            listing = crawl_with_scrapegraph(crawl_url)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return exit_code_for_parser_error(str(e))
    except Exception as e:
        print(f"PARSER_FAILED: {e}", file=sys.stderr)
        return 1

    saved_photos = save_images(listing.image_urls, session_dir / "photos")
    if not saved_photos:
        print("NO_PHOTOS: Could not save any listing photos.", file=sys.stderr)
        return 3

    description = build_description(listing)
    safe_write_text(session_dir / "description.txt", description)

    if args.save_debug_json:
        payload = {
            "source": "FB",
            "source_type": listing.source_type,
            "source_url": listing.source_url,
            "title": listing.title,
            "location": {
                "raw": listing.location_raw,
                "latitude": listing.latitude,
                "longitude": listing.longitude,
            },
            "price_raw": listing.price_raw,
            "rooms": listing.bedrooms,
            "bathrooms": listing.bathrooms,
            "area_sqm": listing.area_sqm,
            "housing_type": listing.housing_type,
            "description": listing.description,
            "amenities": listing.amenities,
            "seller_name": listing.seller_name,
            "photos_local": saved_photos,
            "parsed_at": listing.parsed_at,
            "backend": listing.backend,
            "debug": listing.debug,
        }
        safe_write_text(
            session_dir / "parsed.json",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    canonical_url = listing.source_url
    print(f"OK: {session_dir}")
    print(f"photos: {len(saved_photos)}")
    label = "post" if listing.source_type == "facebook_post" else "Marketplace"
    print(f"SOURCE: Facebook {label} {canonical_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
