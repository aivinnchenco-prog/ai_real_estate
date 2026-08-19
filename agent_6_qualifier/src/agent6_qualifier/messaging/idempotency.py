"""Idempotency stores for Wazzup inbound/outbound."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class ProcessedEventStore:
    """Persist processed inbound message ids to ignore webhook retries."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_inbound (
                    message_id TEXT PRIMARY KEY,
                    seen_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_ids (
                    crm_message_id TEXT PRIMARY KEY,
                    chat_id TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

    def has_inbound(self, message_id: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM processed_inbound WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return row is not None

    def mark_inbound(self, message_id: str, *, seen_at: str) -> bool:
        """Return True if newly marked, False if duplicate."""
        if self.has_inbound(message_id):
            return False
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO processed_inbound(message_id, seen_at) VALUES (?, ?)",
                (message_id, seen_at),
            )
        return True

    def has_outbound(self, crm_message_id: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM outbound_ids WHERE crm_message_id = ?",
                (crm_message_id,),
            ).fetchone()
        return row is not None

    def mark_outbound(self, crm_message_id: str, *, chat_id: str, created_at: str) -> bool:
        if self.has_outbound(crm_message_id):
            return False
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO outbound_ids(crm_message_id, chat_id, created_at) "
                "VALUES (?, ?, ?)",
                (crm_message_id, chat_id, created_at),
            )
        return True


def dump_redacted(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
