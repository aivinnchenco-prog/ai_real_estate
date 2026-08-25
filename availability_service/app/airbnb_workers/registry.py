from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from ..repository import AvailabilityRepository, _iso, _parse_dt, _utcnow
from .models import (
    AirbnbWorker,
    AssignmentReason,
    WorkerAssignment,
    WorkerStatus,
)

logger = logging.getLogger(__name__)

WORKER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS airbnb_workers (
    worker_id TEXT PRIMARY KEY,
    proxy_id TEXT NOT NULL,
    proxy_endpoint TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    profile_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    assigned_count INTEGER NOT NULL DEFAULT 0,
    active_jobs INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    last_success_at TEXT,
    last_failure_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    captcha_count INTEGER NOT NULL DEFAULT 0,
    cooldown_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS airbnb_worker_assignments (
    object_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    assignment_reason TEXT NOT NULL DEFAULT 'INITIAL',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (worker_id) REFERENCES airbnb_workers(worker_id)
);

CREATE INDEX IF NOT EXISTS idx_airbnb_assignments_worker
    ON airbnb_worker_assignments(worker_id);
"""


def ensure_worker_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(WORKER_SCHEMA_SQL)
    _migrate_worker_columns(conn)
    conn.commit()


def _migrate_worker_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(airbnb_workers)").fetchall()}
    for col, ddl in (
        ("last_pricing_success_at", "TEXT"),
        ("last_pricing_failure_at", "TEXT"),
        ("last_pricing_error", "TEXT NOT NULL DEFAULT ''"),
    ):
        if col not in existing:
            conn.execute(f"ALTER TABLE airbnb_workers ADD COLUMN {col} {ddl}")


class AirbnbWorkerRepository:
    def __init__(self, repo: AvailabilityRepository) -> None:
        self._repo = repo
        ensure_worker_schema(repo._conn)

    @property
    def conn(self) -> sqlite3.Connection:
        return self._repo._conn

    def _row_to_worker(self, row: sqlite3.Row) -> AirbnbWorker:
        return AirbnbWorker(
            worker_id=row["worker_id"],
            proxy_id=row["proxy_id"],
            proxy_endpoint=row["proxy_endpoint"],
            user_agent=row["user_agent"],
            profile_path=row["profile_path"],
            status=WorkerStatus(row["status"]),
            assigned_count=int(row["assigned_count"]),
            active_jobs=int(row["active_jobs"]),
            last_used_at=_parse_dt(row["last_used_at"]),
            last_success_at=_parse_dt(row["last_success_at"]),
            last_failure_at=_parse_dt(row["last_failure_at"]),
            consecutive_failures=int(row["consecutive_failures"]),
            captcha_count=int(row["captcha_count"]),
            cooldown_until=_parse_dt(row["cooldown_until"]),
            last_pricing_success_at=_parse_dt(row["last_pricing_success_at"])
            if "last_pricing_success_at" in row.keys()
            else None,
            last_pricing_failure_at=_parse_dt(row["last_pricing_failure_at"])
            if "last_pricing_failure_at" in row.keys()
            else None,
            last_pricing_error=str(row["last_pricing_error"] or "")
            if "last_pricing_error" in row.keys()
            else "",
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def list_workers(self) -> list[AirbnbWorker]:
        rows = self.conn.execute(
            "SELECT * FROM airbnb_workers ORDER BY worker_id ASC"
        ).fetchall()
        return [self._row_to_worker(row) for row in rows]

    def get_worker(self, worker_id: str) -> AirbnbWorker | None:
        row = self.conn.execute(
            "SELECT * FROM airbnb_workers WHERE worker_id = ?",
            (worker_id,),
        ).fetchone()
        return self._row_to_worker(row) if row else None

    def upsert_worker(self, worker: AirbnbWorker, *, now: datetime | None = None) -> None:
        now = now or _utcnow()
        created = worker.created_at or now
        self.conn.execute(
            """
            INSERT INTO airbnb_workers (
                worker_id, proxy_id, proxy_endpoint, user_agent, profile_path,
                status, assigned_count, active_jobs,
                last_used_at, last_success_at, last_failure_at,
                consecutive_failures, captcha_count, cooldown_until,
                last_pricing_success_at, last_pricing_failure_at, last_pricing_error,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                proxy_id=excluded.proxy_id,
                proxy_endpoint=excluded.proxy_endpoint,
                user_agent=excluded.user_agent,
                profile_path=excluded.profile_path,
                status=excluded.status,
                assigned_count=excluded.assigned_count,
                active_jobs=excluded.active_jobs,
                last_used_at=excluded.last_used_at,
                last_success_at=excluded.last_success_at,
                last_failure_at=excluded.last_failure_at,
                consecutive_failures=excluded.consecutive_failures,
                captcha_count=excluded.captcha_count,
                cooldown_until=excluded.cooldown_until,
                last_pricing_success_at=excluded.last_pricing_success_at,
                last_pricing_failure_at=excluded.last_pricing_failure_at,
                last_pricing_error=excluded.last_pricing_error,
                updated_at=excluded.updated_at
            """,
            (
                worker.worker_id,
                worker.proxy_id,
                worker.proxy_endpoint,
                worker.user_agent,
                worker.profile_path,
                worker.status.value,
                worker.assigned_count,
                worker.active_jobs,
                _iso(worker.last_used_at),
                _iso(worker.last_success_at),
                _iso(worker.last_failure_at),
                worker.consecutive_failures,
                worker.captcha_count,
                _iso(worker.cooldown_until),
                _iso(worker.last_pricing_success_at),
                _iso(worker.last_pricing_failure_at),
                worker.last_pricing_error or "",
                _iso(created),
                _iso(now),
            ),
        )
        self.conn.commit()

    def get_assignment(self, object_id: str) -> WorkerAssignment | None:
        row = self.conn.execute(
            "SELECT * FROM airbnb_worker_assignments WHERE object_id = ?",
            (object_id,),
        ).fetchone()
        if not row:
            return None
        return WorkerAssignment(
            object_id=row["object_id"],
            worker_id=row["worker_id"],
            assigned_at=_parse_dt(row["assigned_at"]) or _utcnow(),
            assignment_reason=AssignmentReason(row["assignment_reason"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def list_assignments(self) -> list[WorkerAssignment]:
        rows = self.conn.execute(
            "SELECT * FROM airbnb_worker_assignments ORDER BY object_id ASC"
        ).fetchall()
        out: list[WorkerAssignment] = []
        for row in rows:
            out.append(
                WorkerAssignment(
                    object_id=row["object_id"],
                    worker_id=row["worker_id"],
                    assigned_at=_parse_dt(row["assigned_at"]) or _utcnow(),
                    assignment_reason=AssignmentReason(row["assignment_reason"]),
                    updated_at=_parse_dt(row["updated_at"]),
                )
            )
        return out

    def save_assignment(
        self,
        object_id: str,
        worker_id: str,
        *,
        reason: AssignmentReason,
        now: datetime | None = None,
    ) -> None:
        now = now or _utcnow()
        existing = self.get_assignment(object_id)
        self.conn.execute(
            """
            INSERT INTO airbnb_worker_assignments
                (object_id, worker_id, assigned_at, assignment_reason, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(object_id) DO UPDATE SET
                worker_id=excluded.worker_id,
                assignment_reason=excluded.assignment_reason,
                updated_at=excluded.updated_at
            """,
            (object_id, worker_id, _iso(now), reason.value, _iso(now)),
        )
        if existing and existing.worker_id != worker_id:
            self._adjust_assigned_count(existing.worker_id, -1)
        if not existing or existing.worker_id != worker_id:
            self._adjust_assigned_count(worker_id, +1)
        self.conn.commit()

    def _adjust_assigned_count(self, worker_id: str, delta: int) -> None:
        self.conn.execute(
            """
            UPDATE airbnb_workers
            SET assigned_count = MAX(0, assigned_count + ?),
                updated_at = ?
            WHERE worker_id = ?
            """,
            (delta, _iso(_utcnow()), worker_id),
        )

    def recalculate_assigned_counts(self) -> None:
        self.conn.execute("UPDATE airbnb_workers SET assigned_count = 0")
        rows = self.conn.execute(
            "SELECT worker_id, COUNT(*) AS n FROM airbnb_worker_assignments GROUP BY worker_id"
        ).fetchall()
        now = _iso(_utcnow())
        for row in rows:
            self.conn.execute(
                "UPDATE airbnb_workers SET assigned_count = ?, updated_at = ? WHERE worker_id = ?",
                (int(row["n"]), now, row["worker_id"]),
            )
        self.conn.commit()

    def list_airbnb_object_ids(self) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT object_id FROM availability_objects
            WHERE source = 'AIRBNB' OR object_id LIKE 'A_%'
            ORDER BY object_id ASC
            """
        ).fetchall()
        return [row["object_id"] for row in rows]

    def unassigned_airbnb_objects(self) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT o.object_id FROM availability_objects o
            LEFT JOIN airbnb_worker_assignments a ON a.object_id = o.object_id
            WHERE (o.source = 'AIRBNB' OR o.object_id LIKE 'A_%')
              AND a.object_id IS NULL
            ORDER BY o.object_id ASC
            """
        ).fetchall()
        return [row["object_id"] for row in rows]

    def assignments_for_worker(self, worker_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT object_id FROM airbnb_worker_assignments WHERE worker_id = ? ORDER BY object_id",
            (worker_id,),
        ).fetchall()
        return [row["object_id"] for row in rows]
