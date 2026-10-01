"""Facebook post (/share/p/, permalink, page post) photo and text extraction.

Marketplace item cards are handled in fb_parser.py. A /share/p/ link is a feed
or group post: it has no swipeable Marketplace gallery. Photos live in
attachment JSON, og:image, <img> tags, or a photo viewer that only appears
after the first image is opened.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlencode, urljoin, urlparse

import requests

_POST_SHARE_RE = re.compile(
    r"(?:(?:www|m|mbasic)\.)?facebook\.com/share/p/([A-Za-z0-9_-]+)",
    re.I,
)
_STORY_FBID_RE = re.compile(r"(?:story_fbid|fbid)=([^&/?#]+)", re.I)
_PERMALINK_OWNER_RE = re.compile(r"[?&]id=(\d+)", re.I)
_GROUP_PERMALINK_RE = re.compile(
    r"facebook\.com/groups/([^/?#]+)/(permalink|posts)/([^/?#]+)",
    re.I,
)
_PAGE_POST_RE = re.compile(
    r"facebook\.com/([^/?#]+)/posts/([^/?#]+)",
    re.I,
)
_LISTING_CAPTION_RE = re.compile(
    r"\brent\b|villa|bedroom|спальн|аренда|\bpool\b|condo|\bhouse\b|฿|thb|квартир",
    re.I,
)
_SKIP_PAGE_SLUGS = {
    "share",
    "watch",
    "reel",
    "stories",
    "marketplace",
    "groups",
    "photo",
    "photos",
}

# /share/p/ stays on the post. Marketplace /share/CODE/ still waits for an item.
POST_SHARE_WAIT_JS = r"""js:() => {
  const u = window.location.href || "";
  if (/\/marketplace\/item\/\d+/.test(u)) return true;
  const onPost =
    /\/share\/p\//.test(u) ||
    /\/groups\/[^/]+\/permalink\/\d+/.test(u) ||
    /permalink\.php/.test(u) ||
    /story_fbid=/.test(u) ||
    /\/posts\//.test(u) ||
    /\/photo\//.test(u);
  if (!onPost) return false;
  const html = document.documentElement ? document.documentElement.innerHTML : "";
  const imgs = document.querySelectorAll('img[src*="scontent"], img[src*="fbcdn"]');
  return (
    html.includes("all_subattachments") ||
    html.includes('"subattachments":{"nodes"') ||
    html.includes('"message":{"text"') ||
    !!document.querySelector('meta[property="og:image"]') ||
    imgs.length > 0
  );
}"""

POST_PAGE_JS = r"""
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const junk = /emoji|static\.xx\.fbcdn|rsrc\.php|profile_pic|safe_image\.php/i;
  const push = (list, seen, raw) => {
    const u = String(raw || "").trim();
    if (!u || u.startsWith("data:") || !/^https?:/i.test(u)) return;
    if (junk.test(u)) return;
    if (!/scontent|fbcdn|\.(?:jpe?g|png|webp)(?:\?|$)/i.test(u)) return;
    if (seen.has(u)) return;
    seen.add(u);
    list.push(u);
  };
  const seen = new Set();
  const image_urls = [];
  const og = document.querySelector('meta[property="og:image"]');
  if (og && og.content) push(image_urls, seen, og.content);
  document.querySelectorAll("img").forEach((img) => {
    push(image_urls, seen, img.currentSrc || img.src || img.getAttribute("data-src") || "");
    const srcset = img.getAttribute("srcset") || "";
    if (srcset) push(image_urls, seen, srcset.split(",")[0].trim().split(/\s+/)[0]);
  });
  const expandLabels = /see more|показать|ดูเพิ่ม|ver más|mehr anzeigen|voir plus|แสดงเพิ่ม/i;
  document.querySelectorAll('[role="button"], span, div').forEach((btn) => {
    const t = (btn.innerText || "").trim();
    if (t && expandLabels.test(t) && t.length < 40) {
      try { btn.click(); } catch (e) {}
    }
  });
  await sleep(800);
  let description = "";
  const article = document.querySelector('[role="article"]') || document.querySelector('[role="main"]');
  if (article && article.innerText) description = article.innerText.trim().slice(0, 4000);
  const title = (document.querySelector('meta[property="og:title"]') || {}).content || "";
  return JSON.stringify({
    title,
    description,
    image_urls,
    page_kind: "post"
  });
})();
"""


def _fp():
    from agent1b import fb_parser as fp

    return fp


def is_post_share_url(url: str) -> bool:
    """facebook.com/share/p/{code}/ — a feed/Page/group post, not Marketplace."""
    return bool(_POST_SHARE_RE.search(url or ""))


def facebook_share_p_code(url: str) -> Optional[str]:
    match = _POST_SHARE_RE.search(url or "")
    return match.group(1) if match else None


def facebook_story_fbid(url: str) -> Optional[str]:
    match = _STORY_FBID_RE.search(url or "")
    return match.group(1) if match else None


def _decode_json_string(value: str) -> str:
    value = value.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\/", "/")
    value = re.sub(r"\\u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), value)
    return value.strip()


def facebook_post_anchor(url: str) -> Optional[str]:
    """Id of THIS post in a group/page feed HTML dump."""
    story = facebook_story_fbid(url)
    if story:
        return story
    group = _GROUP_PERMALINK_RE.search(url or "")
    if group:
        return group.group(3).split("?")[0]
    page = _PAGE_POST_RE.search(url or "")
    if page and page.group(1).lower() not in _SKIP_PAGE_SLUGS:
        return page.group(2).split("?")[0]
    return facebook_share_p_code(url)


def _looks_like_facebook_post_url(url: str) -> bool:
    text = url or ""
    if is_post_share_url(text):
        return True
    lower = text.lower()
    if "permalink.php" in lower or "story_fbid=" in lower or "/permalink/" in lower:
        return True
    if _GROUP_PERMALINK_RE.search(text):
        return True
    page = _PAGE_POST_RE.search(text)
    if page and page.group(1).lower() not in _SKIP_PAGE_SLUGS:
        return True
    return False


def canonicalize_facebook_post_url(url: str) -> str:
    """Stable post URL: permalink.php, /posts/, or /share/p/ without tracking."""
    text = (url or "").strip()
    if not text:
        return text
    if text.startswith("/"):
        text = "https://www.facebook.com" + text
    story = facebook_story_fbid(text)
    owner = _PERMALINK_OWNER_RE.search(text)
    if story and owner and ("permalink.php" in text or "story.php" in text):
        return (
            "https://www.facebook.com/permalink.php?"
            + urlencode({"story_fbid": story, "id": owner.group(1)})
        )
    group = _GROUP_PERMALINK_RE.search(text)
    if group:
        post_id = group.group(3).split("?")[0]
        return (
            f"https://www.facebook.com/groups/{group.group(1)}/"
            f"{group.group(2).lower()}/{post_id}"
        )
    page = _PAGE_POST_RE.search(text)
    if page and page.group(1).lower() not in {"share", "watch", "reel", "stories", "marketplace"}:
        return f"https://www.facebook.com/{page.group(1)}/posts/{page.group(2).split('?')[0]}"
    code = facebook_share_p_code(text)
    if code:
        return f"https://www.facebook.com/share/p/{code}/"
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    return text.split("#")[0]


def resolve_share_redirect(url: str, *, timeout: float = 12.0) -> str:
    """Follow the public 302 from /share/… to a Marketplace item or a post.

    Logged-in Chromium often stays on /share/CODE/. A plain HTTP request sees
    the same redirect curl does. /share/p/ that does not redirect is returned
    unchanged so the post crawler can open it.
    """
    fp = _fp()
    if not fp.is_share_url(url):
        return url
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.head(url, headers=headers, allow_redirects=False, timeout=timeout)
        loc = resp.headers.get("Location") or ""
        if resp.status_code >= 400 or not loc:
            resp = requests.get(url, headers=headers, allow_redirects=False, timeout=timeout)
            loc = resp.headers.get("Location") or ""
        candidates = [loc, resp.url or ""]
    except requests.RequestException:
        return url

    from agent1b.fb_page import canonical_item_url, item_id_from_navigation_url

    for cand in candidates:
        if not cand:
            continue
        absolute = urljoin("https://www.facebook.com/", cand)
        item = item_id_from_navigation_url(absolute)
        if item:
            return canonical_item_url(item)
        if _looks_like_facebook_post_url(absolute):
            return canonicalize_facebook_post_url(absolute)
    return url


def _photo_stem(url: str) -> str:
    name = urlparse(url).path.rsplit("/", 1)[-1].split("?")[0]
    parts = name.split("_")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}_{parts[1]}"
    return name[:40]


def facebook_pcb_photo_url(
    html: str,
    page_url: str = "",
    *,
    story_id: Optional[str] = None,
    hint_urls: Optional[list[str]] = None,
) -> Optional[str]:
    """Photo viewer URL for THIS post's pcb album, not a neighbour on the Page."""
    if not html:
        return None
    fp = _fp()
    found: list[tuple[int, str]] = []
    for match in re.finditer(
        r'(?:href="|/photo/\?)(?:https://(?:www\.)?facebook\.com)?/photo/\?fbid=(\d+)[^"\s<>]*?set=pcb\.(\d+)',
        html,
        re.I,
    ):
        url = f"https://www.facebook.com/photo/?fbid={match.group(1)}&set=pcb.{match.group(2)}"
        found.append((match.start(), url))
    if not found:
        for match in re.finditer(
            r'href="(/photo/\?fbid=\d+[^"]*?set=pcb\.\d+[^"]*)"',
            html,
            re.I,
        ):
            href = fp.unescape_fb_url(match.group(1).replace("&amp;", "&"))
            if href.startswith("/"):
                href = "https://www.facebook.com" + href
            found.append((match.start(), href))
    if not found:
        return None

    def cores_from_hints() -> list[str]:
        cores: list[str] = []
        for raw in hint_urls or []:
            stem = _photo_stem(raw)
            parts = stem.split("_")
            if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) >= 8:
                cores.append(parts[1][:8])
            elif parts[0].isdigit() and len(parts[0]) >= 8:
                cores.append(parts[0][:8])
        return cores

    hint_cores = cores_from_hints()
    if hint_cores:
        for _, url in found:
            if any(core in url for core in hint_cores):
                return url

    if story_id:
        containing = [(pos, url) for pos, url in found if story_id in url]
        if containing:
            return containing[0][1]
        idx = html.find(story_id)
        if idx >= 0:
            after = [(pos, url) for pos, url in found if pos >= idx]
            if after:
                return after[0][1]

    set_ids: list[str] = []
    for _, url in found:
        match_set = re.search(r"set=pcb\.(\d+)", url, re.I)
        sid = match_set.group(1) if match_set else url
        if sid not in set_ids:
            set_ids.append(sid)
    if len(set_ids) > 1:
        return None
    return found[0][1]


def _is_listing_caption(text: str) -> bool:
    text = text or ""
    hits = _LISTING_CAPTION_RE.findall(text)
    if len(hits) >= 2:
        return True
    return bool(re.search(r"for rent|for sale|аренда|฿", text, re.I))


def _caption_fingerprint(text: str) -> str:
    return re.sub(r"\s+", " ", _decode_json_string(text or "").lower())[:80]


def _dimensioned_photo_urls(text: str, *, min_side: int = 720) -> list[str]:
    fp = _fp()
    found: list[tuple[int, str]] = []
    patterns = (
        r'"height":(\d{3,5}),"width":(\d{3,5}),"uri":"(https:\\/\\/scontent[^"]+)"',
        r'"width":(\d{3,5}),"height":(\d{3,5}),"uri":"(https:\\/\\/scontent[^"]+)"',
        r'"uri":"(https:\\/\\/scontent[^"]+)","height":(\d{3,5}),"width":(\d{3,5})',
        r'"uri":"(https:\\/\\/scontent[^"]+)","width":(\d{3,5}),"height":(\d{3,5})',
        r'"height":(\d{3,5}),"width":(\d{3,5}),"scale":\d+(?:\.\d+)?,"uri":"(https:\\/\\/scontent[^"]+)"',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            groups = match.groups()
            nums = [g for g in groups if g.isdigit()]
            urls = [g for g in groups if g.startswith("http")]
            if len(nums) < 2 or not urls:
                continue
            a, b = int(nums[0]), int(nums[1])
            if min(a, b) < min_side:
                continue
            found.append((match.start(), fp.unescape_fb_url(urls[0])))
    found.sort(key=lambda item: item[0])
    return fp.dedupe_urls([url for _, url in found])


def _attachment_gallery_blocks(html: str) -> list[tuple[int, str]]:
    key = '"all_subattachments":{'
    starts = [m.start() for m in re.finditer(re.escape(key), html)]
    if not starts:
        key = '"subattachments":{"nodes"'
        starts = [m.start() for m in re.finditer(re.escape(key), html)]
    blocks: list[tuple[int, str]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else min(len(html), start + 120000)
        blocks.append((start, html[start:end]))
    return blocks


def _gallery_from_block(block: str) -> list[str]:
    fp = _fp()
    urls = [u for u in _dimensioned_photo_urls(block, min_side=1000) if not fp.is_junk_image_url(u)]
    count_m = re.search(r'"all_subattachments":\{"count":(\d+)', block)
    if not count_m:
        count_m = re.search(r'^.{0,40}"count":(\d+)', block)
    if count_m:
        cap = int(count_m.group(1))
        if 2 <= cap <= 20:
            urls = urls[:cap]
    return urls


def _single_post_gallery(html: str, story_id: Optional[str] = None) -> list[str]:
    """Photos from THIS post only — neighbouring Page posts stay out."""
    fp = _fp()
    blocks = _attachment_gallery_blocks(html)
    if not blocks:
        return []

    galleries: list[tuple[int, list[str]]] = []
    for start, block in blocks:
        urls = _gallery_from_block(block)
        if len(urls) >= 2:
            galleries.append((start, urls))
    if not galleries:
        return []

    all_long_messages = list(
        re.finditer(r'"message":\{"text":"((?:\\.|[^"\\]){80,8000})"', html)
    )

    anchor: Optional[int] = None
    if story_id:
        idx = html.find(story_id)
        if idx >= 0:
            anchor = idx
    listing_messages = [
        match
        for match in all_long_messages
        if _is_listing_caption(_decode_json_string(match.group(1)))
    ]
    if anchor is None and listing_messages:
        anchor = max(listing_messages, key=lambda m: len(m.group(1))).start()
    elif anchor is None and all_long_messages:
        anchor = max(all_long_messages, key=lambda m: len(m.group(1))).start()

    primary_fp = ""
    if listing_messages:
        nearest_listing = min(
            listing_messages,
            key=lambda m: abs(m.start() - (anchor if anchor is not None else m.start())),
        )
        primary_fp = _caption_fingerprint(nearest_listing.group(1))

    def is_foreign_caption(match: re.Match) -> bool:
        fingerprint = _caption_fingerprint(match.group(1))
        if primary_fp and (fingerprint == primary_fp or fingerprint.startswith(primary_fp[:40])):
            return False
        return True

    foreign_long = [match for match in all_long_messages if is_foreign_caption(match)]

    clusters: list[list[tuple[int, list[str]]]] = []
    for item in galleries:
        if clusters:
            prev_start, prev_urls = clusters[-1][-1]
            gap = item[0] - prev_start
            same_album = bool(
                {_photo_stem(u) for u in prev_urls[:3]}
                & {_photo_stem(u) for u in item[1][:3]}
            )
            boundary = any(prev_start < msg.start() < item[0] for msg in foreign_long)
            id_boundary = bool(
                story_id and anchor is not None and prev_start < anchor <= item[0]
            )
            if same_album or (gap < 80000 and not boundary and not id_boundary):
                clusters[-1].append(item)
                continue
        clusters.append([item])

    def merge(cluster: list[tuple[int, list[str]]]) -> list[str]:
        urls: list[str] = []
        for _, chunk in cluster:
            urls.extend(chunk)
        return fp.dedupe_urls(urls)

    merged_clusters = [(cluster[0][0], merge(cluster)) for cluster in clusters]
    _single_post_gallery.last_debug = {
        "galleries": [
            {"n": len(urls), "start": start, "stem": _photo_stem(urls[0])}
            for start, urls in galleries[:12]
        ],
        "clusters": [len(urls) for _, urls in merged_clusters[:12]],
        "story_id_found": bool(story_id) and bool(story_id in html),
    }

    if anchor is None:
        return max(merged_clusters, key=lambda item: len(item[1]))[1][:15]

    candidates = merged_clusters
    if story_id and story_id in html:
        after = [item for item in merged_clusters if item[0] >= anchor]
        if after:
            candidates = after
    ranked = sorted(candidates, key=lambda item: (abs(item[0] - anchor), -len(item[1])))
    return ranked[0][1][:15]


_single_post_gallery.last_debug = {}


def _meta_content(html: str, prop: str) -> str:
    patterns = (
        rf'<meta[^>]+property=["\']{prop}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{prop}["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return match.group(1).strip()
    return ""


def fallback_post_image_urls(html: str) -> list[str]:
    """og:image and <img> URLs when the post has no attachment album JSON.

    A single photo, a lazy-loaded image, or a layout without a swipeable
    gallery still exposes the picture this way.
    """
    if not html:
        return []
    fp = _fp()
    urls: list[str] = []
    og = _meta_content(html, "og:image")
    if og:
        urls.append(fp.unescape_fb_url(og.replace("&amp;", "&")))
    for match in re.finditer(
        r"""<(?:img|image)\b[^>]*?\b(?:src|data-src|data-lazy-src)\s*=\s*["']([^"']+)["']""",
        html,
        re.I,
    ):
        urls.append(fp.unescape_fb_url(match.group(1).replace("&amp;", "&")))
    for match in re.finditer(r"""\bsrcset\s*=\s*["']([^"']+)["']""", html, re.I):
        first = match.group(1).split(",")[0].strip().split()
        if first:
            urls.append(fp.unescape_fb_url(first[0].replace("&amp;", "&")))
    kept: list[str] = []
    for url in fp.dedupe_urls(urls):
        if fp.is_junk_image_url(url):
            continue
        lower = url.lower()
        if "fbcdn" in lower or "scontent" in lower or any(
            ext in lower for ext in (".jpg", ".jpeg", ".png", ".webp")
        ):
            kept.append(url)
    return kept[:15]


def extract_post_text_from_html(html: str, story_id: Optional[str] = None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not html:
        return out

    og_title_raw = _meta_content(html, "og:title")
    og_desc = _meta_content(html, "og:description")
    og_fp = _caption_fingerprint(og_desc) if og_desc else ""

    messages = list(re.finditer(r'"message":\{"text":"((?:\\.|[^"\\]){20,8000})"', html))
    chosen: re.Match | None = None
    if story_id:
        idx = html.find(story_id)
        if idx >= 0 and messages:
            nearby = sorted(messages, key=lambda m: abs(m.start() - idx))[:6]
            listing_near = [
                m for m in nearby if _is_listing_caption(_decode_json_string(m.group(1)))
            ]
            chosen = (listing_near or nearby)[0]
    if chosen is None and og_fp and messages:
        for match in messages:
            fingerprint = _caption_fingerprint(_decode_json_string(match.group(1)))
            if og_fp[:32] in fingerprint or fingerprint[:32] in og_fp:
                chosen = match
                break
    if chosen is None and messages:
        listing_msgs = [
            m for m in messages if _is_listing_caption(_decode_json_string(m.group(1)))
        ]
        chosen = listing_msgs[0] if listing_msgs else messages[0]
    if chosen:
        out["description"] = _decode_json_string(chosen.group(1))
    elif og_desc and len(og_desc) > 20:
        out["description"] = og_desc
    else:
        for pattern in (
            r'"untranslated_body":\{"text":"((?:\\.|[^"\\]){20,8000})"',
            r'"comet_feed_message":\{"text":"((?:\\.|[^"\\]){20,8000})"',
        ):
            match = re.search(pattern, html)
            if match:
                out["description"] = _decode_json_string(match.group(1))
                break

    seller = re.search(
        r'"(?:owner|actors)"\s*:\s*\{[^{}]{0,400}?"name":"([^"]{2,120})"',
        html,
    )
    if not seller:
        seller = re.search(r'"actors":\[\{[^\[\]]{0,400}?"name":"([^"]{2,120})"', html)
    if seller:
        out["seller"] = _decode_json_string(seller.group(1))

    if og_title_raw:
        title = re.sub(r"\s*\|\s*Facebook\s*$", "", og_title_raw, flags=re.I).strip()
        if title and title.lower() != "facebook":
            out["title"] = title
    if og_desc and "description" not in out and len(og_desc) > 20:
        out["description"] = og_desc
    if "title" not in out and out.get("description"):
        first = out["description"].split("\n")[0].strip()
        if first:
            out["title"] = first[:140]
    return out


def extract_injected_post_gallery(html: str) -> list[str]:
    if not html:
        return []
    fp = _fp()
    match = re.search(
        r'<script[^>]*id=["\']openhome-post-gallery["\'][^>]*>(.*?)</script>',
        html,
        re.I | re.S,
    )
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    urls = [
        url
        for url in data
        if isinstance(url, str) and url.startswith("http") and not fp.is_junk_image_url(url)
    ]
    return fp.dedupe_urls(urls)[:20]


def extract_post_images_from_html(html: str, story_id: Optional[str] = None) -> list[str]:
    if not html:
        return []
    fp = _fp()
    gallery = _single_post_gallery(html, story_id=story_id)
    if len(gallery) >= 2:
        return gallery[:15]

    urls: list[str] = []
    msg = re.search(r'"message":\{"text":"', html)
    start = max(0, msg.start() - 4000) if msg else 0
    end = min(len(html), (msg.end() if msg else 0) + 80000)
    chunk = html[start:end] if msg else html[:120000]
    for pattern in (
        r'"(?:photo_image|viewer_image|large_share_image|full_image)":\{[^{}]{0,400}"(?:uri|url)":"(https:\\/\\/scontent[^"]+)"',
        r'"(?:uri|url)":"(https:\\/\\/scontent[^"]+)"',
    ):
        for raw in re.findall(pattern, chunk):
            urls.append(fp.unescape_fb_url(raw))
    filtered: list[str] = []
    for url in fp.dedupe_urls(urls):
        if fp.is_junk_image_url(url):
            continue
        if "fbcdn" in url.lower() or any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            filtered.append(url)
    if filtered:
        return filtered[:15]
    return fallback_post_image_urls(html)


def apply_swiped_gallery(
    listing: Any,
    swipe: dict[str, Any],
    *,
    prefer_swipe: bool,
) -> None:
    """Merge viewer-swipe URLs into listing photos.

    Posts: a walked album (2+ frames) replaces HTML. An empty swipe or a single
    frame keeps attachment / og:image / <img> URLs already collected.
    Marketplace: swipe may grow the set, never shrink a good static gallery.
    """
    from agent1b.fb_gallery import dedupe_gallery_urls

    fp = _fp()
    listing.debug["gallery_swipe"] = {
        "error": swipe.get("error", ""),
        "opened": swipe.get("opened"),
        "final_url": (swipe.get("final_url") or "")[:300],
        "count": len(swipe.get("urls") or []),
    }
    swiped = [
        u
        for u in (swipe.get("urls") or [])
        if isinstance(u, str) and not fp.is_junk_image_url(u)
    ]
    swiped = dedupe_gallery_urls(swiped)
    if prefer_swipe:
        if swiped and (
            len(swiped) >= 2
            or not listing.image_urls
            or len(swiped) >= len(listing.image_urls)
        ):
            listing.image_urls = swiped
            listing.debug["image_strategy"] = "gallery_swipe_post"
        elif not listing.image_urls:
            listing.debug["image_strategy"] = "swipe_failed_post"
        listing.debug["image_count_detected"] = len(listing.image_urls)
        listing.debug["image_url_sample"] = listing.image_urls[:15]
        return

    if swiped and len(swiped) > len(listing.image_urls):
        listing.image_urls = swiped
        listing.debug["image_strategy"] = "gallery_swipe"
    elif swiped and listing.image_urls:
        merged = dedupe_gallery_urls(listing.image_urls + swiped)
        if len(merged) > len(listing.image_urls):
            listing.image_urls = merged
            listing.debug["image_strategy"] = "static_plus_swipe"
    elif swiped and not listing.image_urls:
        listing.image_urls = swiped
        listing.debug["image_strategy"] = "gallery_swipe"
    elif not listing.image_urls:
        listing.debug["image_strategy"] = "swipe_failed_no_static"

    listing.debug["image_count_detected"] = len(listing.image_urls)
    listing.debug["image_url_sample"] = listing.image_urls[:15]


async def merge_gallery_swipe(
    listing: Any,
    gallery_url: str,
    *,
    prefer_swipe: bool,
) -> None:
    from agent1b.fb_gallery import gallery_swipe_enabled, scrape_gallery_from_url

    if not gallery_swipe_enabled() or not (gallery_url or "").strip():
        return
    swipe = await scrape_gallery_from_url(gallery_url)
    apply_swiped_gallery(listing, swipe, prefer_swipe=prefer_swipe)


def apply_post_listing(
    listing: Any,
    *,
    html: str,
    js_payload: dict[str, Any],
    result: Any,
    fallback_url: str,
) -> Any:
    fp = _fp()
    listing.source_type = "facebook_post"
    listing.debug["page_kind"] = "post"
    html_fields = extract_post_text_from_html(
        html,
        story_id=facebook_post_anchor(str(js_payload.get("canonical_url") or ""))
        or facebook_post_anchor(fallback_url)
        or facebook_share_p_code(fallback_url),
    )
    listing.source_url = canonicalize_facebook_post_url(
        str(js_payload.get("canonical_url") or fallback_url or listing.source_url)
    )

    js_desc = (js_payload.get("description") or "").strip()
    html_desc = (html_fields.get("description") or "").strip()
    chosen_desc = html_desc if len(html_desc) > len(js_desc) + 40 else (js_desc or html_desc)

    listing.title = (
        (js_payload.get("title") or "").strip()
        or html_fields.get("title", "").strip()
        or listing.title
    )
    generic_titles = {
        "facebook",
        "marketplace",
        "facebook marketplace",
        "facebook marketplace listing",
        "facebook post",
    }
    if listing.title.lower() in generic_titles:
        first = (chosen_desc or "").split("\n")[0].strip()
        listing.title = first[:140] if len(first) > 8 else "Facebook Post"

    listing.price_raw = (js_payload.get("price") or html_fields.get("price") or "").strip()
    listing.location_raw = (js_payload.get("location") or html_fields.get("location") or "").strip()
    listing.seller_name = (js_payload.get("seller") or html_fields.get("seller") or "").strip()
    listing.description = fp.pick_listing_description(chosen_desc)
    listing.debug["description_source"] = (
        "html_json" if chosen_desc == html_desc and html_desc else "js" if js_desc else "none"
    )
    fp.enrich_from_text(
        listing,
        "\n".join([listing.title, listing.price_raw, listing.description]),
    )

    listing.debug["js_keys"] = sorted(list(js_payload.keys()))
    listing.debug["html_len"] = len(html)
    listing.debug["has_all_subattachments"] = '"all_subattachments"' in (html or "")
    story = (
        facebook_post_anchor(listing.source_url)
        or facebook_post_anchor(fallback_url)
        or facebook_share_p_code(fallback_url)
    )
    html_gallery = extract_post_images_from_html(html, story_id=story)
    listing.debug["html_gallery_count"] = len(html_gallery)
    listing.debug["gallery_layout"] = getattr(_single_post_gallery, "last_debug", {})
    injected = extract_injected_post_gallery(html)
    listing.debug["injected_gallery_count"] = len(injected)
    js_imgs: list[str] = []
    for url in js_payload.get("image_urls") or []:
        if isinstance(url, str) and not fp.is_junk_image_url(url):
            js_imgs.append(url)
    listing.debug["js_gallery_count"] = len(js_imgs)
    ranked = [
        (injected, "post_viewer"),
        (js_imgs, "post_js"),
        (html_gallery, "post_html_attachments"),
    ]
    ranked.sort(key=lambda item: len(item[0]), reverse=True)
    image_urls, strategy = ranked[0]
    listing.debug["image_strategy"] = strategy if image_urls else "post_html"
    listing.debug["image_url_sample"] = image_urls[:12]

    if not image_urls:
        media = getattr(result, "media", None)
        if isinstance(media, dict) and "images" in media:
            for img in media["images"] or []:
                url = ""
                if isinstance(img, dict):
                    url = img.get("src") or img.get("url") or ""
                elif isinstance(img, str):
                    url = img
                if url and not fp.is_junk_image_url(url):
                    image_urls.append(url)
        if image_urls:
            listing.debug["image_strategy"] = "crawl4ai_media_filtered"
        else:
            image_urls = fallback_post_image_urls(html)
            if image_urls:
                listing.debug["image_strategy"] = "post_og_or_img"

    listing.image_urls = fp.dedupe_urls(image_urls)
    listing.debug["image_count_detected"] = len(listing.image_urls)
    if listing.image_urls:
        listing.image_urls = [
            u for u in listing.image_urls if not fp.is_junk_image_url(u)
        ] or listing.image_urls
    return listing
