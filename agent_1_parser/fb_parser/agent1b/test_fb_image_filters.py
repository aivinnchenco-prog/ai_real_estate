"""Unit tests for Marketplace gallery image junk / promo filters."""

from __future__ import annotations

import struct
from pathlib import Path

from agent1b.fb_parser import (
    filter_minority_square_promos,
    is_junk_image_bytes,
    is_junk_image_url,
    _image_dimensions,
)


def _jpeg_bytes(width: int, height: int, fill: bytes = b"\xff") -> bytes:
    """Minimal-ish JPEG with SOF0 so _image_dimensions can read size."""
    out = bytearray(b"\xff\xd8")
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    out += b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
    sof_body = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    out += b"\xff\xc0" + struct.pack(">H", len(sof_body) + 2) + sof_body
    sos = b"\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00"
    out += b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos
    out += fill * max(0, 22_000 - len(out))
    out += b"\xff\xd9"
    return bytes(out)


def test_junk_url_rejects_225_tile() -> None:
    assert is_junk_image_url("https://scontent.xx.fbcdn.net/v/t39/p225x225/123_n.jpg")
    assert is_junk_image_url("https://scontent.xx.fbcdn.net/v/t39/s261x260/abc.jpg")
    assert is_junk_image_url("https://external.fbcdn.net/safe_image.php?url=x")


def test_junk_url_keeps_large_gallery() -> None:
    assert not is_junk_image_url(
        "https://scontent.xx.fbcdn.net/v/t39.30808-6/123_n.jpg?_nc_cat=1"
    )
    assert not is_junk_image_url(
        "https://scontent.xx.fbcdn.net/v/t39/s960x960/villa.jpg"
    )


def test_image_dimensions_jpeg() -> None:
    data = _jpeg_bytes(960, 720)
    assert _image_dimensions(data) == (960, 720)


def test_junk_bytes_rejects_tiny_square() -> None:
    tiny = _jpeg_bytes(225, 225)
    assert is_junk_image_bytes(tiny) is True


def test_junk_bytes_keeps_large_photo() -> None:
    big = _jpeg_bytes(960, 720, fill=b"\xaa")
    assert is_junk_image_bytes(big) is False


def test_filter_minority_square_promos(tmp_path: Path) -> None:
    photos = tmp_path
    saved: list[str] = []
    # 6 landscape listing photos + 2 square promo creatives
    for i in range(1, 7):
        name = f"photo_{i:03d}.jpg"
        (photos / name).write_bytes(_jpeg_bytes(960, 720))
        saved.append(f"photos/{name}")
    for i in (7, 8):
        name = f"photo_{i:03d}.jpg"
        (photos / name).write_bytes(_jpeg_bytes(960, 960))
        saved.append(f"photos/{name}")

    kept = filter_minority_square_promos(saved, photos)
    assert len(kept) == 6
    assert all("photo_007" not in p and "photo_008" not in p for p in kept)
    assert not (photos / "photo_007.jpg").exists()
    assert (photos / "photo_001.jpg").exists()


def test_filter_keeps_all_square_listing(tmp_path: Path) -> None:
    photos = tmp_path
    saved: list[str] = []
    for i in range(1, 7):
        name = f"photo_{i:03d}.jpg"
        (photos / name).write_bytes(_jpeg_bytes(1080, 1080))
        saved.append(f"photos/{name}")
    kept = filter_minority_square_promos(saved, photos)
    assert len(kept) == 6
