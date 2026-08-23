"""Prepare listing photos for Telegram albums.

Facebook Marketplace often serves progressive JPEGs (SOF2). Telegram then
rejects a media group with IMAGE_PROCESS_FAILED (typically "message #N").
"""

from __future__ import annotations

import io
import re
import struct
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, Message
from aiogram.utils.media_group import MediaGroupBuilder

from CustomLogger import logger

TG_ALBUM_LIMIT = 10
TG_PHOTO_MAX_BYTES = 9_500_000
TG_PHOTO_MIN_BYTES = 80
TG_PHOTO_MAX_SIDE = 2560
_FAILED_INDEX_RE = re.compile(r"message #(\d+)", re.IGNORECASE)


def telegram_failed_media_index(exc: BaseException) -> int | None:
    """0-based index of the album item Telegram named in IMAGE_PROCESS_FAILED."""
    match = _FAILED_INDEX_RE.search(str(exc))
    if not match:
        return None
    return max(0, int(match.group(1)) - 1)


def is_progressive_jpeg(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if data[:2] != b"\xff\xd8":
        return False
    i = 2
    while i < len(data) - 8:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xC2:  # SOF2 progressive
            return True
        if marker in (0xC0, 0xC1):
            return False
        if marker in (0xD8, 0xD9, 0x00):
            i += 2
            continue
        try:
            seglen = struct.unpack(">H", data[i + 2 : i + 4])[0]
        except struct.error:
            return False
        i += 2 + seglen
    return False


def prepare_telegram_photo(path: str | Path) -> Path | None:
    """Return a baseline JPEG Telegram can ingest, or None to skip."""
    src = Path(path)
    try:
        if not src.is_file() or src.stat().st_size < TG_PHOTO_MIN_BYTES:
            return None
        if src.stat().st_size > TG_PHOTO_MAX_BYTES and not _has_pillow():
            return None
    except OSError:
        return None

    needs_reencode = is_progressive_jpeg(src) or src.suffix.lower() in {".webp", ".png", ".gif"}
    if not needs_reencode and src.stat().st_size <= TG_PHOTO_MAX_BYTES:
        return src
    converted = _reencode_baseline_jpeg(src)
    if converted is not None:
        return converted
    if src.stat().st_size > TG_PHOTO_MAX_BYTES:
        return None
    return src


def _has_pillow() -> bool:
    try:
        import PIL.Image  # noqa: F401

        return True
    except ImportError:
        return False


def _reencode_baseline_jpeg(src: Path) -> Path | None:
    try:
        from PIL import Image
    except ImportError:
        logger.warning(f"Pillow missing — cannot convert {src.name} for Telegram")
        return None
    try:
        with Image.open(src) as img:
            img.load()
            if img.mode != "RGB":
                img = img.convert("RGB")
            w, h = img.size
            longest = max(w, h)
            if longest > TG_PHOTO_MAX_SIDE:
                scale = TG_PHOTO_MAX_SIDE / float(longest)
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True, progressive=False)
        data = buf.getvalue()
        if len(data) < TG_PHOTO_MIN_BYTES or len(data) > TG_PHOTO_MAX_BYTES:
            return None
        out = src.with_name(src.stem + ".tg.jpg")
        out.write_bytes(data)
        return out
    except Exception as exc:
        logger.warning(f"Telegram photo convert failed {src}: {exc}")
        return None


async def send_photo_batches(message: Message, paths: list[str]) -> tuple[int, int]:
    """Send photos in albums of 10. Returns (sent, skipped). Does not raise for bad images."""
    prepared: list[Path] = []
    skipped = 0
    for raw in paths:
        ready = prepare_telegram_photo(raw)
        if ready is None:
            skipped += 1
            logger.warning(f"Skip photo for Telegram: {raw}")
            continue
        prepared.append(ready)

    sent = 0
    i = 0
    while i < len(prepared):
        batch = prepared[i : i + TG_ALBUM_LIMIT]
        try:
            _ok, used, dropped = await _send_album_or_split(message, batch)
        except Exception as exc:
            logger.error(f"Telegram album batch failed hard: {exc}")
            used, dropped = 0, len(batch)
        sent += used
        skipped += dropped
        i += len(batch)
    return sent, skipped


async def _send_album_or_split(message: Message, batch: list[Path]) -> tuple[bool, int, int]:
    if not batch:
        return True, 0, 0
    if len(batch) == 1:
        return await _send_one(message, batch[0])
    media = MediaGroupBuilder(caption="")
    for path in batch:
        media.add_photo(media=FSInputFile(path))
    try:
        await message.answer_media_group(media=media.build())
        return True, len(batch), 0
    except TelegramBadRequest as exc:
        logger.warning(f"Telegram album failed ({exc}); splitting")
        bad_at = telegram_failed_media_index(exc)
        sent = 0
        skipped = 0
        if bad_at is not None and 0 <= bad_at < len(batch):
            before, bad, after = batch[:bad_at], batch[bad_at], batch[bad_at + 1 :]
            if before:
                _ok, used, drop = await _send_album_or_split(message, before)
                sent += used
                skipped += drop
            _ok, used, drop = await _send_one(message, bad)
            sent += used
            skipped += drop
            if after:
                _ok, used, drop = await _send_album_or_split(message, after)
                sent += used
                skipped += drop
            return True, sent, skipped
        for path in batch:
            _ok, used, drop = await _send_one(message, path)
            sent += used
            skipped += drop
        return True, sent, skipped


async def _send_one(message: Message, path: Path) -> tuple[bool, int, int]:
    try:
        await message.answer_photo(FSInputFile(path))
        return True, 1, 0
    except TelegramBadRequest as exc:
        logger.warning(f"Telegram photo failed {path.name}: {exc}; trying document")
        try:
            await message.answer_document(FSInputFile(path))
            return True, 1, 0
        except TelegramBadRequest as exc2:
            logger.error(f"Skip photo {path}: {exc2}")
            return True, 0, 1
