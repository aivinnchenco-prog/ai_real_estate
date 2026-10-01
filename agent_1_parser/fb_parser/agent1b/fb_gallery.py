#!/usr/bin/env python3
"""Collect listing/post photos by opening the Facebook photo viewer and swiping."""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

from agent1b.fb_session import (
    get_profile_path,
    human_delay,
    parse_proxy_from_env,
    profile_lock,
    resolve_headless,
)

# Fingerprint of a FB CDN photo (same file at different sizes → one id).
_PHOTO_FILE_RE = re.compile(r"/(\d+_\d+_\d+_n)\.[a-z]+", re.I)

_OPEN_VIEWER_JS = r"""
() => {
  const junk = /emoji|static\.xx\.fbcdn|rsrc\.php|profile_pic|safe_image\.php|s200x200|s160x160|s100x100|s50x50/i;
  const related = /similar|related|more from|you may also|sponsored|suggested|похож|ещё от/i;

  const isExcluded = (img) => {
    let el = img;
    for (let i = 0; i < 14 && el; i++) {
      if (el.tagName === "A") {
        const href = el.getAttribute("href") || "";
        const m = href.match(/marketplace\/item\/(\d+)/);
        const cur = (location.pathname.match(/marketplace\/item\/(\d+)/) || [])[1];
        if (m && cur && m[1] !== cur) return true;
      }
      const aria = (el.getAttribute && el.getAttribute("aria-label")) || "";
      const txt = ((el.innerText || "") + " " + aria).slice(0, 180);
      if (related.test(txt) && el.querySelectorAll && el.querySelectorAll("img").length >= 2) {
        const links = el.querySelectorAll('a[href*="/marketplace/item/"]');
        if (links.length >= 2) return true;
      }
      el = el.parentElement;
    }
    return false;
  };

  const scored = [];
  for (const img of document.querySelectorAll("img")) {
    const s = img.currentSrc || img.src || "";
    if (!/^https?:/i.test(s)) continue;
    if (!/scontent|fbcdn/i.test(s)) continue;
    if (junk.test(s)) continue;
    if (isExcluded(img)) continue;
    const r = img.getBoundingClientRect();
    if (r.width < 140 || r.height < 140) continue;
    if (r.bottom < 0 || r.top > innerHeight) continue;
    scored.push({ img, area: r.width * r.height });
  }
  scored.sort((a, b) => b.area - a.area);
  if (!scored.length) return { ok: false, reason: "no_clickable_photo" };
  scored[0].img.setAttribute("data-oh-gallery-hit", "1");
  try {
    scored[0].img.click();
    return { ok: true, area: scored[0].area };
  } catch (e) {
    return { ok: true, area: scored[0].area, click_error: String(e) };
  }
}
"""

_CURRENT_VIEWER_URL_JS = r"""
() => {
  const junk = /emoji|static\.xx\.fbcdn|rsrc\.php|profile_pic|safe_image\.php|s200x200|s160x160|s100x100|s50x50/i;
  const pick = (root) => {
    if (!root) return "";
    const imgs = [];
    for (const img of root.querySelectorAll("img")) {
      const s = img.currentSrc || img.src || "";
      if (!/^https?:/i.test(s)) continue;
      if (!/scontent|fbcdn/i.test(s)) continue;
      if (junk.test(s)) continue;
      const w = img.naturalWidth || img.width || 0;
      const h = img.naturalHeight || img.height || 0;
      if ((w > 0 && w < 280) || (h > 0 && h < 280)) continue;
      const area = (w || 400) * (h || 400);
      imgs.push({ s, area });
    }
    imgs.sort((a, b) => b.area - a.area);
    return imgs.length ? imgs[0].s : "";
  };
  // Group permalink already lives in a dialog — do not use the first dialog.
  const preferred = pick(document.querySelector('[data-pagelet="MediaViewerPhoto"]'))
    || pick(document.querySelector('[aria-label*="Photo viewer"]'))
    || pick(document.querySelector('[aria-label*="Viewer"]'));
  if (preferred) return preferred;
  let best = "";
  let bestArea = 0;
  for (const root of document.querySelectorAll('[role="dialog"]')) {
    const s = pick(root);
    if (!s) continue;
    const img = Array.from(root.querySelectorAll("img")).find((el) => (el.currentSrc || el.src) === s);
    const area = img ? (img.naturalWidth || 400) * (img.naturalHeight || 400) : 0;
    if (area > bestArea) {
      bestArea = area;
      best = s;
    }
  }
  return best;
}
"""


def gallery_swipe_enabled() -> bool:
    return os.getenv("FB_GALLERY_SWIPE", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def gallery_max_photos() -> int:
    raw = os.getenv("FB_GALLERY_MAX", "40").strip()
    try:
        return max(1, min(80, int(raw)))
    except ValueError:
        return 40


def gallery_page_settled(url: str) -> bool:
    """True when the current URL is a listing/post page we can swipe."""
    cur = (url or "").lower()
    return (
        "/marketplace/item/" in cur
        or "/posts/" in cur
        or "/photo" in cur
        or "/permalink/" in cur
        or "/permalink.php" in cur
        or "/share/p/" in cur
    )


def is_continue_as_label(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return bool(
        re.search(r"continue as|ดำเนินการต่อในชื่อ|продолжить как|繼續以", t)
    )


async def _dismiss_continue_as(page) -> bool:
    """Headless Chromium often gets Facebook's 'Continue as NAME' interstitial."""
    clicked = False
    try:
        btn = page.get_by_role("button", name=re.compile(r"continue as|ดำเนินการต่อ", re.I))
        if await btn.count():
            await btn.first.click(timeout=2500)
            clicked = True
    except Exception:
        pass
    if not clicked:
        try:
            clicked = bool(
                await page.evaluate(
                    """() => {
                      const btn = Array.from(document.querySelectorAll('[role="button"], button')).find((el) => {
                        const t = ((el.innerText || "") + " " + (el.getAttribute("aria-label") || ""));
                        return /continue as|ดำเนินการต่อในชื่อ|продолжить как|繼續以/i.test(t);
                      });
                      if (!btn) return false;
                      try { btn.click(); return true; } catch (e) { return false; }
                    }"""
                )
            )
        except Exception:
            clicked = False
    if clicked:
        await asyncio.sleep(1.5)
    return bool(clicked)


def photo_fingerprint(url: str) -> str:
    if not url:
        return ""
    m = _PHOTO_FILE_RE.search(url)
    if m:
        return m.group(1)
    path = urlparse(url).path
    return path or url.split("?", 1)[0]


def dedupe_gallery_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if not url or not url.startswith("http"):
            continue
        fp = photo_fingerprint(url)
        if fp in seen:
            continue
        seen.add(fp)
        out.append(url)
    return out


async def _wait_viewer(page, timeout_ms: int = 15000) -> bool:
    selectors = [
        '[data-pagelet="MediaViewerPhoto"] img[src*="scontent"]',
        '[aria-label*="Photo viewer"] img[src*="scontent"]',
        '[aria-label*="Viewer"] img[src*="scontent"]',
    ]
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        cur = (page.url or "").lower()
        if "/photo" in cur:
            loaded = await page.evaluate(_CURRENT_VIEWER_URL_JS)
            if loaded:
                return True
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if await loc.count():
                    return True
            except Exception:
                pass
        await asyncio.sleep(0.25)
    return False


async def _read_viewer_url(page) -> str:
    for _ in range(8):
        url = await page.evaluate(_CURRENT_VIEWER_URL_JS)
        if url:
            return str(url)
        await asyncio.sleep(0.2)
    return ""


async def _click_next(page) -> bool:
    """Advance gallery. Prefer Next-photo button, then ArrowRight."""
    try:
        clicked = await page.evaluate(
            """() => {
              const btn = Array.from(document.querySelectorAll('[role="button"]')).find((el) => {
                const a = (el.getAttribute("aria-label") || "").toLowerCase();
                return /next photo|наступн|следующ|світлин|photo suivante|nächste foto|foto successiva|ถัดไป/.test(a);
              });
              if (!btn) return false;
              try { btn.click(); return true; } catch (e) { return false; }
            }"""
        )
        if clicked:
            return True
    except Exception:
        pass

    try:
        await page.keyboard.press("ArrowRight")
        return True
    except Exception:
        pass

    next_names = re.compile(
        r"^(Next|Next photo|Next media|Следующ|Далее|Наступн|ถัดไป|下一|Weiter|Suivant)$",
        re.I,
    )
    try:
        btn = page.get_by_role("button", name=next_names)
        if await btn.count():
            await btn.first.click(timeout=2000)
            return True
    except Exception:
        pass
    return False


async def collect_gallery_by_swiping(page, *, max_photos: int | None = None) -> list[str]:
    """Open photo viewer on current page and swipe through the album."""
    limit = max_photos if max_photos is not None else gallery_max_photos()
    opened = await page.evaluate(_OPEN_VIEWER_JS)
    if not (isinstance(opened, dict) and opened.get("ok")):
        return []
    try:
        hit = page.locator('img[data-oh-gallery-hit="1"]')
        if await hit.count():
            await hit.first.click(timeout=4000)
    except Exception:
        pass

    if not await _wait_viewer(page):
        return []

    await asyncio.sleep(1.0)
    urls: list[str] = []
    first_fp = ""

    for i in range(limit):
        current = await _read_viewer_url(page)
        if not current:
            break
        fp = photo_fingerprint(current)
        if i == 0:
            first_fp = fp
        elif fp and fp == first_fp and urls:
            # Full circle — album complete.
            break
        if fp and any(photo_fingerprint(u) == fp for u in urls):
            # Stuck / duplicate without advancing — stop.
            if i > 0:
                break
        else:
            urls.append(current)

        advanced = await _click_next(page)
        if not advanced:
            break
        await asyncio.sleep(0.55 + min(0.35, i * 0.02))

        # Escape hatch: if URL did not change after next, stop.
        nxt = await _read_viewer_url(page)
        if nxt and photo_fingerprint(nxt) == fp and i > 0:
            # try once more with a longer wait
            await asyncio.sleep(0.7)
            nxt2 = await _read_viewer_url(page)
            if nxt2 and photo_fingerprint(nxt2) == fp:
                break

    # Close viewer so the page is usable again.
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass

    return dedupe_gallery_urls(urls)


async def scrape_gallery_from_url(url: str, *, max_photos: int | None = None) -> dict[str, Any]:
    """Open URL in the saved FB profile and swipe the photo viewer."""
    from playwright.async_api import async_playwright

    result: dict[str, Any] = {
        "urls": [],
        "final_url": "",
        "opened": False,
        "error": "",
    }
    if not gallery_swipe_enabled():
        result["error"] = "disabled"
        return result

    profile_path = get_profile_path()
    headless = resolve_headless(for_login=False)
    proxy = parse_proxy_from_env()
    launch_args: dict[str, Any] = {
        "headless": headless,
        "viewport": {"width": 1440, "height": 900},
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    if proxy:
        launch_args["proxy"] = proxy.to_playwright()

    async with profile_lock():
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(profile_path),
                **launch_args,
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                await human_delay()
                # Share / soft redirects (group permalink, Marketplace, posts)
                for _ in range(12):
                    if gallery_page_settled(page.url):
                        break
                    await asyncio.sleep(0.5)
                result["final_url"] = page.url
                await asyncio.sleep(1.0)
                await _dismiss_continue_as(page)
                urls = await collect_gallery_by_swiping(page, max_photos=max_photos)
                result["urls"] = urls
                result["opened"] = bool(urls)
            except Exception as exc:
                result["error"] = str(exc)[:400]
            finally:
                await context.close()
                await asyncio.sleep(0.5)

    return result
