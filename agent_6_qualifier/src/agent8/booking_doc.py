"""Compatibility shim. Canonical import: ``agent8.notary.booking_doc``."""
import subprocess  # noqa: F401 — tests monkeypatch booking_doc.subprocess

from agent8.notary import booking_doc as _canonical
from agent8.notary.booking_doc import (
    RESERVATION_VALID_DAYS,
    build_booking_data,
    generate_booking_doc,
)

_OUT_DIR = _canonical._OUT_DIR
_SCRIPT = _canonical._SCRIPT

__all__ = [
    "RESERVATION_VALID_DAYS",
    "build_booking_data",
    "generate_booking_doc",
    "_OUT_DIR",
    "_SCRIPT",
    "subprocess",
]
