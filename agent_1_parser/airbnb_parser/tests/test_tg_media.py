#!/usr/bin/env python3
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

_AIRBNB_ROOT = Path(__file__).resolve().parents[1]
if str(_AIRBNB_ROOT) not in sys.path:
    sys.path.insert(0, str(_AIRBNB_ROOT))

from tg_media import is_progressive_jpeg, telegram_failed_media_index


def _jpeg(sof_marker: int, width: int = 960, height: int = 640) -> bytes:
    out = bytearray(b"\xff\xd8")
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    out += b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
    sof_body = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    out += bytes([0xFF, sof_marker]) + struct.pack(">H", len(sof_body) + 2) + sof_body
    sos = b"\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00"
    out += b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos
    out += b"\x00" * 32
    out += b"\xff\xd9"
    return bytes(out)


class TelegramMediaTest(unittest.TestCase):
    def test_failed_index_is_zero_based(self):
        err = Exception(
            'Telegram server says - Bad Request: failed to send message #8 '
            'with the error message "IMAGE_PROCESS_FAILED"'
        )
        self.assertEqual(telegram_failed_media_index(err), 7)

    def test_detects_progressive_jpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            progressive = Path(tmp) / "p.jpg"
            baseline = Path(tmp) / "b.jpg"
            progressive.write_bytes(_jpeg(0xC2))
            baseline.write_bytes(_jpeg(0xC0))
            self.assertTrue(is_progressive_jpeg(progressive))
            self.assertFalse(is_progressive_jpeg(baseline))


if __name__ == "__main__":
    unittest.main()
