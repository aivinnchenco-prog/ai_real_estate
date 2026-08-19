"""SQLite persistence scoped to Агент 10/data/ only."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent10_marketer.models import ApprovalRecord, ApprovalState


SCHEMA = """
CREATE TABLE IF NOT EXISTS publications_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    organic_score REAL NOT NULL,
    daily_budget REAL NOT NULL,
    duration_days INTEGER NOT NULL,
    max_total_budget REAL NOT NULL,
    state TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    notes TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns_future (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS performance_future (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Agent10Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def save_publications(self, object_id: str, publications: list[dict[str, Any]]) -> None:
        now = _now()
        with self._connect() as conn:
            for pub in publications:
                conn.execute(
                    "INSERT INTO publications_snapshot (object_id, publication_id, payload_json, captured_at) "
                    "VALUES (?, ?, ?, ?)",
                    (object_id, pub.get("publication_id", ""), json.dumps(pub, ensure_ascii=False), now),
                )

    def save_analytics(self, object_id: str, analytics: list[dict[str, Any]]) -> None:
        now = _now()
        with self._connect() as conn:
            for row in analytics:
                conn.execute(
                    "INSERT INTO analytics_snapshot (publication_id, object_id, payload_json, captured_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        row.get("publication_id", ""),
                        object_id,
                        json.dumps(row, ensure_ascii=False),
                        now,
                    ),
                )

    def save_recommendation(self, object_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO recommendations (object_id, payload_json, created_at) VALUES (?, ?, ?)",
                (object_id, json.dumps(payload, ensure_ascii=False), _now()),
            )

    def save_approval(self, record: ApprovalRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO approvals (
                    approval_id, object_id, publication_id, organic_score,
                    daily_budget, duration_days, max_total_budget, state,
                    approved_by, approved_at, created_at, notes, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.approval_id,
                    record.object_id,
                    record.publication_id,
                    record.organic_score,
                    record.daily_budget,
                    record.duration_days,
                    record.max_total_budget,
                    record.state.value,
                    record.approved_by,
                    None if record.approved_at is None else record.approved_at.isoformat(),
                    None if record.created_at is None else record.created_at.isoformat(),
                    record.notes,
                    json.dumps(record.to_dict(), ensure_ascii=False),
                ),
            )

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        if row is None:
            return None
        return ApprovalRecord(
            approval_id=row["approval_id"],
            object_id=row["object_id"],
            publication_id=row["publication_id"],
            organic_score=row["organic_score"],
            daily_budget=row["daily_budget"],
            duration_days=row["duration_days"],
            max_total_budget=row["max_total_budget"],
            state=ApprovalState(row["state"]),
            approved_by=row["approved_by"],
            approved_at=(
                datetime.fromisoformat(row["approved_at"]) if row["approved_at"] else None
            ),
            created_at=(
                datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            ),
            notes=row["notes"],
        )
