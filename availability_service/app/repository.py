from __future__ import annotations

import shutil
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import (
    AvailabilityFreshness,
    AvailabilityObjectState,
    AvailabilityStatus,
    CalendarDay,
    DailyAvailabilityStatus,
    MonthAvailability,
    MonthlyRow,
    PropertySource,
    RefreshJob,
    RefreshStatus,
    RefreshTier,
    SourceKind,
    SourceStatus,
    TIER_HOURS,
    calendar_day,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS availability_objects (
    object_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'UNKNOWN',
    source_url TEXT NOT NULL DEFAULT '',
    notion_page_id TEXT NOT NULL DEFAULT '',
    refresh_tier TEXT NOT NULL DEFAULT '12H',
    last_checked_at TEXT,
    next_check_at TEXT,
    refresh_status TEXT NOT NULL DEFAULT 'IDLE',
    source_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    last_error TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_calendar_refresh_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (object_id) REFERENCES availability_objects(object_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_refresh_jobs_active
    ON refresh_jobs(object_id)
    WHERE status IN ('QUEUED', 'CHECKING');

CREATE INDEX IF NOT EXISTS idx_refresh_jobs_object
    ON refresh_jobs(object_id, created_at);

CREATE TABLE IF NOT EXISTS service_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS availability_calendar_days (
    object_id TEXT NOT NULL,
    date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    source TEXT NOT NULL DEFAULT 'UNKNOWN',
    source_state TEXT,
    fetched_at TEXT NOT NULL,
    last_successful_refresh TEXT,
    available INTEGER,
    PRIMARY KEY (object_id, date),
    FOREIGN KEY (object_id) REFERENCES availability_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_calendar_object_date
    ON availability_calendar_days(object_id, date);

CREATE TABLE IF NOT EXISTS availability_months (
    object_id TEXT NOT NULL,
    month_key TEXT NOT NULL,
    status TEXT NOT NULL,
    price REAL,
    currency TEXT NOT NULL DEFAULT 'THB',
    pricing_status TEXT,
    period_used TEXT,
    based_on_days INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (object_id, month_key),
    FOREIGN KEY (object_id) REFERENCES availability_objects(object_id)
);

CREATE INDEX IF NOT EXISTS idx_months_object
    ON availability_months(object_id, month_key);
"""

def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _iso_date(value: date) -> str:
    return value.isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _availability_to_display_status(status: AvailabilityStatus) -> str:
    if status == AvailabilityStatus.AVAILABLE:
        return "FULLY_AVAILABLE"
    if status == AvailabilityStatus.UNAVAILABLE:
        return "UNAVAILABLE"
    return "UNKNOWN"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class AvailabilityRepository:
    last_migration_backup: str = ""
    last_integrity_before: str = ""
    last_integrity_after: str = ""

    def __init__(self, sqlite_path: str | Path) -> None:
        self.sqlite_path = Path(sqlite_path)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.sqlite_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self.initialize()

    def initialize(self) -> None:
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()
        self._migrate_schema()
        self._migrate_airbnb_worker_schema()
        self._repair_calendar_sources_if_needed()
        self._conn.commit()

    def _migrate_airbnb_worker_schema(self) -> None:
        from .airbnb_workers.registry import ensure_worker_schema

        ensure_worker_schema(self._conn)

    def _table_columns(self, table: str) -> set[str]:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}

    def _integrity_check(self) -> str:
        row = self._conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "fail"

    def _backup_database(self) -> str:
        if not self.sqlite_path.exists():
            return ""
        backups_dir = self.sqlite_path.parent / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = _utcnow().strftime("%Y%m%dT%H%M%SZ")
        backup = backups_dir / f"availability.sqlite3.backup-{stamp}"
        shutil.copy2(self.sqlite_path, backup)
        AvailabilityRepository.last_migration_backup = str(backup)
        return str(backup)

    def _migrate_schema(self) -> None:
        """Backward-compatible migration for daily availability canonical model."""
        self.last_integrity_before = self._integrity_check()
        calendar_cols = self._table_columns("availability_calendar_days")
        object_cols = self._table_columns("availability_objects")
        needs_calendar = "status" not in calendar_cols
        needs_object_col = "last_calendar_refresh_at" not in object_cols
        needs_status_reason = "status_reason" not in object_cols
        if not needs_calendar and not needs_object_col and not needs_status_reason:
            self.last_integrity_after = self.last_integrity_before
            return

        self._backup_database()
        if needs_status_reason:
            self._conn.execute(
                "ALTER TABLE availability_objects ADD COLUMN status_reason TEXT NOT NULL DEFAULT ''"
            )
        if needs_object_col:
            self._conn.execute(
                "ALTER TABLE availability_objects ADD COLUMN last_calendar_refresh_at TEXT"
            )
        if needs_calendar:
            if "status" not in self._table_columns("availability_calendar_days"):
                self._conn.execute(
                    "ALTER TABLE availability_calendar_days ADD COLUMN status TEXT NOT NULL DEFAULT 'UNKNOWN'"
                )
            if "source" not in self._table_columns("availability_calendar_days"):
                self._conn.execute(
                    "ALTER TABLE availability_calendar_days ADD COLUMN source TEXT NOT NULL DEFAULT 'UNKNOWN'"
                )
            if "last_successful_refresh" not in self._table_columns("availability_calendar_days"):
                self._conn.execute(
                    "ALTER TABLE availability_calendar_days ADD COLUMN last_successful_refresh TEXT"
                )
            self._conn.execute(
                """
                UPDATE availability_calendar_days
                SET status = CASE
                    WHEN available = 1 THEN 'AVAILABLE'
                    WHEN available = 0 THEN 'BLOCKED'
                    ELSE 'UNKNOWN'
                END
                WHERE (status IS NULL OR status = 'UNKNOWN') AND available IS NOT NULL
                """
            )
            self._conn.execute(
                """
                UPDATE availability_calendar_days
                SET last_successful_refresh = fetched_at
                WHERE last_successful_refresh IS NULL AND fetched_at IS NOT NULL
                """
            )
            self._migrate_calendar_sources_from_metadata()
        self._conn.commit()
        self._sync_object_calendar_refresh_from_days()
        self.last_integrity_after = self._integrity_check()
        self._conn.commit()

    def _infer_calendar_source_for_object(self, object_id: str) -> SourceKind:
        """Resolve calendar row source from object metadata or object_id convention."""
        state = self.get_object(object_id)
        if state is not None and state.source != SourceKind.UNKNOWN:
            return state.source
        oid = object_id.strip().upper()
        if oid.startswith("A_"):
            return SourceKind.AIRBNB
        if oid.startswith("F_"):
            return SourceKind.FACEBOOK
        return SourceKind.UNKNOWN

    def _migrate_calendar_sources_from_metadata(self) -> None:
        """Set legacy UNKNOWN calendar sources per object — never blanket AIRBNB."""
        rows = self._conn.execute(
            "SELECT DISTINCT object_id FROM availability_calendar_days"
        ).fetchall()
        for row in rows:
            object_id = row["object_id"]
            source = self._infer_calendar_source_for_object(object_id)
            self._conn.execute(
                """
                UPDATE availability_calendar_days
                SET source = ?
                WHERE object_id = ?
                  AND (source IS NULL OR source = '' OR source = 'UNKNOWN')
                """,
                (source.value, object_id),
            )

    def _repair_calendar_sources_if_needed(self) -> None:
        """Repair UNKNOWN sources on existing DBs using metadata (safe re-run)."""
        rows = self._conn.execute(
            """
            SELECT DISTINCT object_id FROM availability_calendar_days
            WHERE source IS NULL OR source = '' OR source = 'UNKNOWN'
            """
        ).fetchall()
        for row in rows:
            object_id = row["object_id"]
            source = self._infer_calendar_source_for_object(object_id)
            if source == SourceKind.UNKNOWN:
                continue
            self._conn.execute(
                """
                UPDATE availability_calendar_days
                SET source = ?
                WHERE object_id = ? AND (source IS NULL OR source = '' OR source = 'UNKNOWN')
                """,
                (source.value, object_id),
            )

    def _sync_object_calendar_refresh_from_days(self) -> None:
        rows = self._conn.execute(
            """
            SELECT object_id, MAX(last_successful_refresh) AS mx
            FROM availability_calendar_days
            GROUP BY object_id
            """
        ).fetchall()
        for row in rows:
            if not row["mx"]:
                continue
            self._conn.execute(
                """
                UPDATE availability_objects
                SET last_calendar_refresh_at = ?
                WHERE object_id = ? AND (last_calendar_refresh_at IS NULL OR last_calendar_refresh_at < ?)
                """,
                (row["mx"], row["object_id"], row["mx"]),
            )

    def close(self) -> None:
        self._conn.close()

    def upsert_property(self, item: PropertySource, *, now: datetime | None = None,
                        default_tier: RefreshTier = RefreshTier.H48) -> AvailabilityObjectState:
        now = now or _utcnow()
        existing = self.get_object(item.object_id)
        if existing is None:
            return self.register_new_property(item, now=now, default_tier=default_tier)

        existing.name = item.name
        existing.source = item.source
        existing.source_url = item.source_url
        existing.notion_page_id = item.notion_page_id or existing.notion_page_id
        self._update_object(existing, updated_at=now)
        return existing

    def register_new_property(
        self,
        item: PropertySource,
        *,
        now: datetime | None = None,
        default_tier: RefreshTier = RefreshTier.H48,
    ) -> AvailabilityObjectState:
        """New object: FIRST_REFRESH tier, due immediately for first live refresh."""
        now = now or _utcnow()
        state = AvailabilityObjectState(
            object_id=item.object_id,
            name=item.name,
            source=item.source,
            source_url=item.source_url,
            notion_page_id=item.notion_page_id,
            refresh_tier=RefreshTier.FIRST_REFRESH,
            last_checked_at=None,
            next_check_at=now,
            refresh_status=RefreshStatus.IDLE,
            source_status=SourceStatus.UNKNOWN,
            last_error="",
            retry_count=0,
        )
        self._insert_object(state, created_at=now, updated_at=now)
        return state

    def set_next_check_at(
        self,
        object_id: str,
        next_check_at: datetime,
        *,
        now: datetime | None = None,
    ) -> None:
        now = now or _utcnow()
        self._conn.execute(
            """
            UPDATE availability_objects
            SET next_check_at = ?, updated_at = ?
            WHERE object_id = ?
            """,
            (_iso(next_check_at), _iso(now), object_id),
        )
        self._conn.commit()

    def set_refresh_tier(
        self,
        object_id: str,
        tier: RefreshTier,
        *,
        now: datetime | None = None,
    ) -> None:
        now = now or _utcnow()
        self._conn.execute(
            """
            UPDATE availability_objects
            SET refresh_tier = ?, updated_at = ?
            WHERE object_id = ?
            """,
            (tier.value, _iso(now), object_id),
        )
        self._conn.commit()

    def set_notion_page_id(
        self,
        object_id: str,
        page_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        now = now or _utcnow()
        self._conn.execute(
            """
            UPDATE availability_objects
            SET notion_page_id = ?, updated_at = ?
            WHERE object_id = ?
            """,
            (page_id, _iso(now), object_id),
        )
        self._conn.commit()

    def _insert_object(self, state: AvailabilityObjectState, *, created_at: datetime,
                       updated_at: datetime) -> None:
        self._conn.execute(
            """
            INSERT INTO availability_objects (
                object_id, name, source, source_url, notion_page_id,
                refresh_tier, last_checked_at, next_check_at, refresh_status,
                source_status, last_error, retry_count, last_calendar_refresh_at,
                status_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.object_id,
                state.name,
                state.source.value,
                state.source_url,
                state.notion_page_id,
                state.refresh_tier.value,
                _iso(state.last_checked_at),
                _iso(state.next_check_at),
                state.refresh_status.value,
                state.source_status.value,
                state.last_error,
                state.retry_count,
                _iso(state.last_calendar_refresh_at),
                state.status_reason,
                _iso(created_at),
                _iso(updated_at),
            ),
        )
        self._conn.commit()

    def _update_object(self, state: AvailabilityObjectState, *, updated_at: datetime) -> None:
        self._conn.execute(
            """
            UPDATE availability_objects SET
                name=?, source=?, source_url=?, notion_page_id=?,
                refresh_tier=?, last_checked_at=?, next_check_at=?,
                refresh_status=?, source_status=?, last_error=?,
                retry_count=?, last_calendar_refresh_at=?, status_reason=?, updated_at=?
            WHERE object_id=?
            """,
            (
                state.name,
                state.source.value,
                state.source_url,
                state.notion_page_id,
                state.refresh_tier.value,
                _iso(state.last_checked_at),
                _iso(state.next_check_at),
                state.refresh_status.value,
                state.source_status.value,
                state.last_error,
                state.retry_count,
                _iso(state.last_calendar_refresh_at),
                state.status_reason,
                _iso(updated_at),
                state.object_id,
            ),
        )
        self._conn.commit()

    def save_object(self, state: AvailabilityObjectState, *, now: datetime | None = None) -> None:
        now = now or _utcnow()
        if self.get_object(state.object_id) is None:
            self._insert_object(state, created_at=now, updated_at=now)
        else:
            self._update_object(state, updated_at=now)

    def remove_object(self, object_id: str) -> dict[str, int]:
        """Delete object and all related rows (calendar, months, jobs). Idempotent."""
        deleted: dict[str, int] = {}
        for table in (
            "refresh_jobs",
            "availability_calendar_days",
            "availability_months",
        ):
            cur = self._conn.execute(
                f"DELETE FROM {table} WHERE object_id = ?",
                (object_id,),
            )
            deleted[table] = cur.rowcount
        cur = self._conn.execute(
            "DELETE FROM availability_objects WHERE object_id = ?",
            (object_id,),
        )
        deleted["availability_objects"] = cur.rowcount
        self._conn.commit()
        return deleted

    def get_object(self, object_id: str) -> AvailabilityObjectState | None:
        row = self._conn.execute(
            "SELECT * FROM availability_objects WHERE object_id = ?",
            (object_id,),
        ).fetchone()
        return self._row_to_state(row) if row else None

    def list_objects(self) -> list[AvailabilityObjectState]:
        rows = self._conn.execute(
            "SELECT * FROM availability_objects ORDER BY next_check_at IS NULL, next_check_at, object_id"
        ).fetchall()
        return [self._row_to_state(row) for row in rows]

    def due_objects(self, *, now: datetime | None = None) -> list[AvailabilityObjectState]:
        now = now or _utcnow()
        rows = self._conn.execute(
            """
            SELECT * FROM availability_objects
            WHERE next_check_at IS NOT NULL AND next_check_at <= ?
            ORDER BY next_check_at ASC, object_id ASC
            """,
            (_iso(now),),
        ).fetchall()
        return [self._row_to_state(row) for row in rows]

    def enqueue_job(self, object_id: str, *, now: datetime | None = None) -> tuple[RefreshJob, bool]:
        """Create a QUEUED job if none is already QUEUED/CHECKING. Returns (job, created)."""
        now = now or _utcnow()
        existing = self.active_job(object_id)
        if existing is not None:
            return existing, False
        try:
            cur = self._conn.execute(
                """
                INSERT INTO refresh_jobs (object_id, status, created_at, error)
                VALUES (?, 'QUEUED', ?, '')
                """,
                (object_id, _iso(now)),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            existing = self.active_job(object_id)
            if existing is None:
                raise
            return existing, False
        job = RefreshJob(
            job_id=cur.lastrowid,
            object_id=object_id,
            status=RefreshStatus.QUEUED,
            created_at=now,
        )
        state = self.get_object(object_id)
        if state is not None:
            state.refresh_status = RefreshStatus.QUEUED
            self._update_object(state, updated_at=now)
        return job, True

    def active_job(self, object_id: str) -> RefreshJob | None:
        row = self._conn.execute(
            """
            SELECT * FROM refresh_jobs
            WHERE object_id = ? AND status IN ('QUEUED', 'CHECKING')
            ORDER BY id DESC LIMIT 1
            """,
            (object_id,),
        ).fetchone()
        return self._row_to_job(row) if row else None

    def count_active_jobs(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM refresh_jobs WHERE status IN ('QUEUED', 'CHECKING')"
        ).fetchone()
        return int(row["n"])

    def mark_job_checking(self, job_id: int, *, now: datetime | None = None) -> None:
        now = now or _utcnow()
        row = self._conn.execute("SELECT object_id FROM refresh_jobs WHERE id = ?", (job_id,)).fetchone()
        self._conn.execute(
            "UPDATE refresh_jobs SET status='CHECKING', started_at=? WHERE id=?",
            (_iso(now), job_id),
        )
        if row:
            self._conn.execute(
                "UPDATE availability_objects SET refresh_status='CHECKING', updated_at=? WHERE object_id=?",
                (_iso(now), row["object_id"]),
            )
        self._conn.commit()

    def mark_job_success(self, object_id: str, *, next_check_at: datetime,
                         now: datetime | None = None) -> None:
        now = now or _utcnow()
        self._conn.execute(
            """
            UPDATE refresh_jobs SET status='SUCCESS', finished_at=?, error=''
            WHERE object_id=? AND status IN ('QUEUED', 'CHECKING')
            """,
            (_iso(now), object_id),
        )
        self._conn.execute(
            """
            UPDATE availability_objects SET
                refresh_status='SUCCESS', last_checked_at=?, next_check_at=?,
                last_error='', retry_count=0, updated_at=?
            WHERE object_id=?
            """,
            (_iso(now), _iso(next_check_at), _iso(now), object_id),
        )
        self._conn.commit()

    def mark_job_error(self, object_id: str, error: str, *, next_check_at: datetime,
                       now: datetime | None = None) -> int:
        now = now or _utcnow()
        self._conn.execute(
            """
            UPDATE refresh_jobs SET status='ERROR', finished_at=?, error=?
            WHERE object_id=? AND status IN ('QUEUED', 'CHECKING')
            """,
            (_iso(now), error, object_id),
        )
        self._conn.execute(
            """
            UPDATE availability_objects SET
                refresh_status='ERROR', last_checked_at=?, next_check_at=?,
                last_error=?, retry_count=retry_count+1, updated_at=?
            WHERE object_id=?
            """,
            (_iso(now), _iso(next_check_at), error, _iso(now), object_id),
        )
        self._conn.commit()
        state = self.get_object(object_id)
        return state.retry_count if state else 0

    def set_state(self, key: str, value: str, *, now: datetime | None = None) -> None:
        now = now or _utcnow()
        self._conn.execute(
            """
            INSERT INTO service_state (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, _iso(now)),
        )
        self._conn.commit()

    def get_state(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM service_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def counts_by_source(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT source, COUNT(*) AS n FROM availability_objects GROUP BY source"
        ).fetchall()
        return {row["source"]: int(row["n"]) for row in rows}

    def counts_by_tier(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT refresh_tier, COUNT(*) AS n FROM availability_objects GROUP BY refresh_tier"
        ).fetchall()
        return {row["refresh_tier"]: int(row["n"]) for row in rows}

    def upsert_calendar_days(
        self,
        object_id: str,
        days: list[CalendarDay],
        *,
        fetched_at: datetime | None = None,
        last_successful_refresh: datetime | None = None,
    ) -> int:
        """Upsert calendar days; returns number of rows written."""
        fetched_at = fetched_at or _utcnow()
        refresh_at = last_successful_refresh or fetched_at
        fetched_iso = _iso(fetched_at)
        refresh_iso = _iso(refresh_at)
        count = 0
        for day in days:
            normalized = self._normalize_calendar_day(object_id, day, fetched_at, refresh_at)
            self._conn.execute(
                """
                INSERT INTO availability_calendar_days (
                    object_id, date, status, source, source_state,
                    fetched_at, last_successful_refresh, available
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(object_id, date) DO UPDATE SET
                    status=excluded.status,
                    source=excluded.source,
                    source_state=excluded.source_state,
                    fetched_at=excluded.fetched_at,
                    last_successful_refresh=excluded.last_successful_refresh,
                    available=excluded.available
                """,
                (
                    object_id,
                    _iso_date(normalized.date),
                    normalized.status.value,
                    normalized.source.value,
                    normalized.source_state,
                    fetched_iso,
                    refresh_iso,
                    1 if normalized.status == DailyAvailabilityStatus.AVAILABLE else 0,
                ),
            )
            count += 1
        if count:
            self._touch_object_calendar_refresh(object_id, refresh_at)
        self._conn.commit()
        return count

    def replace_calendar_days(
        self,
        object_id: str,
        days: list[CalendarDay],
        *,
        fetched_at: datetime | None = None,
        last_successful_refresh: datetime | None = None,
    ) -> int:
        """Replace full stored calendar for object (fresh provider snapshot)."""
        self._conn.execute(
            "DELETE FROM availability_calendar_days WHERE object_id = ?",
            (object_id,),
        )
        self._conn.commit()
        return self.upsert_calendar_days(
            object_id,
            days,
            fetched_at=fetched_at,
            last_successful_refresh=last_successful_refresh,
        )

    def replace_calendar_range(
        self,
        object_id: str,
        date_from: date,
        date_to: date,
        days: list[CalendarDay],
        *,
        fetched_at: datetime | None = None,
        last_successful_refresh: datetime | None = None,
    ) -> int:
        """Replace calendar days only within [date_from, date_to) — other objects untouched."""
        self._conn.execute(
            """
            DELETE FROM availability_calendar_days
            WHERE object_id = ? AND date >= ? AND date < ?
            """,
            (object_id, _iso_date(date_from), _iso_date(date_to)),
        )
        self._conn.commit()
        return self.upsert_calendar_days(
            object_id,
            days,
            fetched_at=fetched_at,
            last_successful_refresh=last_successful_refresh,
        )

    def get_calendar_days(
        self,
        object_id: str,
        date_from: date,
        date_to: date,
    ) -> list[CalendarDay]:
        """Days with date in [date_from, date_to) — matches stay night range."""
        rows = self._conn.execute(
            """
            SELECT date, status, source, source_state, fetched_at,
                   last_successful_refresh, available
            FROM availability_calendar_days
            WHERE object_id = ? AND date >= ? AND date < ?
            ORDER BY date ASC
            """,
            (object_id, _iso_date(date_from), _iso_date(date_to)),
        ).fetchall()
        return [
            self._row_to_calendar_day(row)
            for row in rows
            if _parse_date(row["date"]) is not None
        ]

    def get_last_calendar_refresh(self, object_id: str) -> datetime | None:
        row = self._conn.execute(
            """
            SELECT MAX(last_successful_refresh) AS mx
            FROM availability_calendar_days
            WHERE object_id = ?
            """,
            (object_id,),
        ).fetchone()
        if row and row["mx"]:
            return _parse_dt(row["mx"])
        state = self.get_object(object_id)
        if state and state.last_calendar_refresh_at:
            return state.last_calendar_refresh_at
        row2 = self._conn.execute(
            "SELECT MAX(fetched_at) AS mx FROM availability_calendar_days WHERE object_id = ?",
            (object_id,),
        ).fetchone()
        if row2 and row2["mx"]:
            return _parse_dt(row2["mx"])
        return None

    def get_refresh_tier(self, object_id: str) -> RefreshTier:
        state = self.get_object(object_id)
        return state.refresh_tier if state else RefreshTier.H48

    def get_object_calendar_freshness(
        self,
        object_id: str,
        *,
        now: datetime | None = None,
    ) -> AvailabilityFreshness:
        """
        MISSING only when calendar rows = 0 for object_id.
        Rows exist + no timestamp → STALE (not MISSING).
        """
        now = now or _utcnow()
        row_count = self.count_calendar_days(object_id)
        if row_count == 0:
            return AvailabilityFreshness.MISSING

        last = self.get_last_calendar_refresh(object_id)
        if last is None:
            return AvailabilityFreshness.STALE

        tier = self.get_refresh_tier(object_id)
        from .scheduler_policy import success_interval_hours

        hours = success_interval_hours(tier)
        if last + timedelta(hours=hours) >= now:
            return AvailabilityFreshness.FRESH
        return AvailabilityFreshness.STALE

    def validate_freshness_invariant(self, object_id: str) -> None:
        """Enforce: rows>0 ↔ freshness≠MISSING; rows=0 ↔ MISSING."""
        count = self.count_calendar_days(object_id)
        freshness = self.get_object_calendar_freshness(object_id)
        if count > 0 and freshness == AvailabilityFreshness.MISSING:
            raise ValueError(
                f"freshness invariant violated: {object_id} has {count} rows but freshness=MISSING"
            )
        if count == 0 and freshness != AvailabilityFreshness.MISSING:
            raise ValueError(
                f"freshness invariant violated: {object_id} has 0 rows but freshness={freshness.value}"
            )

    def _normalize_calendar_day(
        self,
        object_id: str,
        day: CalendarDay,
        fetched_at: datetime,
        refresh_at: datetime,
    ) -> CalendarDay:
        source = day.source
        if source == SourceKind.UNKNOWN:
            source = self._infer_calendar_source_for_object(object_id)
        return calendar_day(
            day.date,
            status=day.status,
            source=source,
            source_state=day.source_state,
            fetched_at=day.fetched_at or fetched_at,
            last_successful_refresh=day.last_successful_refresh or refresh_at,
        )

    def _touch_object_calendar_refresh(self, object_id: str, refresh_at: datetime) -> None:
        self._conn.execute(
            """
            UPDATE availability_objects
            SET last_calendar_refresh_at = ?, updated_at = ?
            WHERE object_id = ?
            """,
            (_iso(refresh_at), _iso(_utcnow()), object_id),
        )

    def _row_to_calendar_day(self, row: sqlite3.Row) -> CalendarDay:
        parsed_date = _parse_date(row["date"])
        if parsed_date is None:
            raise ValueError(f"invalid calendar date: {row['date']}")
        status_raw = row["status"] if "status" in row.keys() and row["status"] else None
        if status_raw:
            status = DailyAvailabilityStatus(status_raw)
        else:
            available_val = row["available"]
            if available_val is None:
                status = DailyAvailabilityStatus.UNKNOWN
            else:
                status = (
                    DailyAvailabilityStatus.AVAILABLE
                    if int(available_val) == 1
                    else DailyAvailabilityStatus.BLOCKED
                )
        source_raw = row["source"] if "source" in row.keys() and row["source"] else SourceKind.UNKNOWN.value
        try:
            source = SourceKind(source_raw)
        except ValueError:
            source = SourceKind.UNKNOWN
        return calendar_day(
            parsed_date,
            status=status,
            source=source,
            source_state=row["source_state"],
            fetched_at=_parse_dt(row["fetched_at"]),
            last_successful_refresh=_parse_dt(
                row["last_successful_refresh"] if "last_successful_refresh" in row.keys() else None
            ),
        )

    def count_calendar_days(self, object_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM availability_calendar_days WHERE object_id = ?",
            (object_id,),
        ).fetchone()
        return int(row["n"])

    def count_monthly_rows(self, object_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM availability_months WHERE object_id = ?",
            (object_id,),
        ).fetchone()
        return int(row["n"])

    def replace_monthly_rows(
        self,
        object_id: str,
        months: list[MonthAvailability],
        *,
        fetched_at: datetime | None = None,
        display_statuses: dict[str, str] | None = None,
    ) -> int:
        """Replace monthly snapshot for object. display_statuses maps month_key → FULLY_AVAILABLE etc."""
        fetched_at = fetched_at or _utcnow()
        fetched_iso = _iso(fetched_at)
        self._conn.execute(
            "DELETE FROM availability_months WHERE object_id = ?",
            (object_id,),
        )
        count = 0
        for item in months:
            month_key = f"{item.year:04d}-{item.month:02d}"
            if display_statuses and month_key in display_statuses:
                status = display_statuses[month_key]
            elif item.note and item.note in {
                "FULLY_AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNKNOWN"
            }:
                status = item.note
            else:
                status = _availability_to_display_status(item.status)
            self._conn.execute(
                """
                INSERT INTO availability_months (
                    object_id, month_key, status, price, currency,
                    pricing_status, period_used, based_on_days, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    object_id,
                    month_key,
                    status,
                    item.price,
                    item.currency or "THB",
                    item.pricing_status,
                    item.period_used,
                    item.based_on_days,
                    fetched_iso,
                ),
            )
            count += 1
        self._conn.commit()
        return count

    def get_monthly_rows(self, object_id: str) -> list[MonthlyRow]:
        rows = self._conn.execute(
            """
            SELECT object_id, month_key, status, price, currency,
                   pricing_status, period_used, based_on_days, fetched_at
            FROM availability_months
            WHERE object_id = ?
            ORDER BY month_key ASC
            """,
            (object_id,),
        ).fetchall()
        return [self._row_to_monthly(row) for row in rows]

    def _row_to_monthly(self, row: sqlite3.Row) -> MonthlyRow:
        return MonthlyRow(
            object_id=row["object_id"],
            month_key=row["month_key"],
            status=row["status"],
            price=row["price"],
            currency=row["currency"] or "THB",
            pricing_status=row["pricing_status"],
            period_used=row["period_used"],
            based_on_days=row["based_on_days"],
            fetched_at=_parse_dt(row["fetched_at"]),
        )

    def _row_to_state(self, row: sqlite3.Row) -> AvailabilityObjectState:
        last_cal = None
        if "last_calendar_refresh_at" in row.keys():
            last_cal = _parse_dt(row["last_calendar_refresh_at"])
        return AvailabilityObjectState(
            object_id=row["object_id"],
            name=row["name"],
            source=SourceKind(row["source"]),
            source_url=row["source_url"],
            notion_page_id=row["notion_page_id"],
            refresh_tier=RefreshTier(row["refresh_tier"]),
            last_checked_at=_parse_dt(row["last_checked_at"]),
            next_check_at=_parse_dt(row["next_check_at"]),
            refresh_status=RefreshStatus(row["refresh_status"]),
            source_status=SourceStatus(row["source_status"]),
            last_error=row["last_error"],
            retry_count=int(row["retry_count"]),
            last_calendar_refresh_at=last_cal,
            status_reason=row["status_reason"] if "status_reason" in row.keys() else "",
        )

    def _row_to_job(self, row: sqlite3.Row) -> RefreshJob:
        return RefreshJob(
            job_id=row["id"],
            object_id=row["object_id"],
            status=RefreshStatus(row["status"]),
            created_at=_parse_dt(row["created_at"]),
            started_at=_parse_dt(row["started_at"]),
            finished_at=_parse_dt(row["finished_at"]),
            error=row["error"],
        )
