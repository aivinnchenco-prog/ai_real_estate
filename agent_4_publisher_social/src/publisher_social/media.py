from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "publisher-social/0.1"


def parse_gallery_image_urls(gallery_url: str) -> list[str]:
    if not gallery_url:
        return []
    if re.search(r"\.(jpe?g|png|webp|gif)(\?.*)?$", gallery_url, re.I):
        return [gallery_url]

    request = urllib.request.Request(gallery_url, method="GET")
    request.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(request, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    base = gallery_url if gallery_url.endswith("/") else gallery_url.rsplit("/", 1)[0] + "/"
    urls: list[str] = []
    seen: set[str] = set()
    for src in re.findall(r"""<img[^>]+src=["']([^"']+)["']""", html, re.I):
        full = src if src.startswith("http") else urllib.parse.urljoin(base, src)
        if full in seen:
            continue
        if not re.search(r"\.(jpe?g|png|webp|gif)(\?.*)?$", full, re.I):
            continue
        seen.add(full)
        urls.append(full)
    return urls


def hook_cover_url(gallery_url: str) -> str | None:
    if not gallery_url or "/photos/" not in gallery_url:
        return None
    base = gallery_url.rsplit("/photos/", 1)[0]
    for candidate in (f"{base}/hook_cover.jpg", f"{base}/photos/000_hook_cover.jpg"):
        request = urllib.request.Request(candidate, method="HEAD")
        request.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(request, timeout=15) as resp:
                if resp.status == 200:
                    return candidate
        except (urllib.error.URLError, OSError):
            continue
    return None


def resolve_image_urls(
    gallery_url: str,
    max_images: int,
    *,
    hook_cover_first: bool = True,
) -> list[str]:
    if max_images <= 0 or not gallery_url:
        return []
    urls = parse_gallery_image_urls(gallery_url)
    if hook_cover_first:
        cover = hook_cover_url(gallery_url)
        if cover:
            urls = [cover] + [u for u in urls if u != cover]
    return urls[:max_images]


def resolve_carousel_urls(carousel_url: str) -> list[str]:
    """Все изображения из carousel_url (index.html на R2), без обрезки."""
    if not carousel_url:
        return []
    return parse_gallery_image_urls(carousel_url)


def resolve_brand_open_home_urls(brand_url: str, max_images: int) -> list[str]:
    """Галерея brand_open_home_url — только для Facebook Marketplace."""
    if max_images <= 0 or not brand_url:
        return []
    return parse_gallery_image_urls(brand_url)[:max_images]


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    request = urllib.request.Request(url, method="GET")
    request.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(request, timeout=300) as resp:
        dest.write_bytes(resp.read())
    return dest


def filename_from_url(url: str, fallback: str) -> str:
    path = urllib.parse.urlparse(url).path
    name = Path(path).name
    if name and "." in name:
        return name
    return fallback


def download_job_media(
    *,
    object_id: str,
    video_url: str | None,
    image_urls: list[str],
    cache_dir: Path,
    marketplace_image_urls: list[str] | None = None,
) -> tuple[str | None, list[str], list[str]]:
    obj_dir = cache_dir / object_id
    obj_dir.mkdir(parents=True, exist_ok=True)

    local_video: str | None = None
    if video_url:
        name = filename_from_url(video_url, "video.mp4")
        local_video = str(download_file(video_url, obj_dir / name))

    local_images: list[str] = []
    for i, url in enumerate(image_urls, start=1):
        name = filename_from_url(url, f"photo_{i:03d}.jpg")
        local_images.append(str(download_file(url, obj_dir / name)))

    local_mp: list[str] = []
    brand_dir = obj_dir / "brand_open_home"
    for i, url in enumerate(marketplace_image_urls or [], start=1):
        name = filename_from_url(url, f"photo_{i:03d}.jpg")
        local_mp.append(str(download_file(url, brand_dir / name)))

    return local_video, local_images, local_mp