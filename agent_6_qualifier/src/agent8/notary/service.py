"""Compatibility shim. Canonical import: ``agent8_notary.service``."""
from agent8_notary.service import (
    AttachFile,
    ErrorHandler,
    GenerateDoc,
    NotaryBookingResult,
    RunInThread,
    SendDoc,
    process_confirmed_booking,
)

__all__ = [
    "AttachFile",
    "ErrorHandler",
    "GenerateDoc",
    "NotaryBookingResult",
    "RunInThread",
    "SendDoc",
    "process_confirmed_booking",
]
