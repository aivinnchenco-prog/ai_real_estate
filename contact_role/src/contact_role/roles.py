"""Canonical contact roles (source of truth)."""

from __future__ import annotations

from enum import Enum


class CanonicalRole(str, Enum):
    CLIENT = "CLIENT"
    OWNER = "OWNER"
    AGENT = "AGENT"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, value: str | CanonicalRole | None) -> CanonicalRole:
        if value is None:
            return cls.UNKNOWN
        if isinstance(value, CanonicalRole):
            return value
        text = str(value).strip().upper()
        if not text:
            return cls.UNKNOWN
        aliases = {
            "CLIENT": cls.CLIENT,
            "КЛИЕНТ": cls.CLIENT,
            "OWNER": cls.OWNER,
            "ВЛАДЕЛЕЦ": cls.OWNER,
            "СОБСТВЕННИК": cls.OWNER,
            "AGENT": cls.AGENT,
            "АГЕНТ": cls.AGENT,
            "UNKNOWN": cls.UNKNOWN,
            "УТОЧНЯЕТСЯ": cls.UNKNOWN,
            "НЕ ОПРЕДЕЛЕНО": cls.UNKNOWN,
        }
        return aliases.get(text, cls.UNKNOWN)
