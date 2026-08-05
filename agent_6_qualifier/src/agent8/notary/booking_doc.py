"""Compatibility shim. Canonical import: ``agent8_notary.booking_doc``."""
from agent8_notary import booking_doc as _canonical
from agent8_notary.booking_doc import (
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
]
