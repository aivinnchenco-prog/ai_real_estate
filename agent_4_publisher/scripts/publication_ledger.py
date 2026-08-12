"""Canonical Publication Ledger for Agent 4 Publisher.

Durable history: 1 object → many publications.
Current/latest pointer remains in postmypost_publication_state.py (JSON).

Agent 10 is a read-only consumer of this store (IG/FB filter applied there).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Confirmed in postmypost_client.py / fixtures — do not invent other mappings.
RAW_STATUS_PUBLISHED = 1
RAW_STATUS_PENDING = 5
PUBLICATION_TYPE_POST = 1
PUBLICATION_TYPE_REELS = 4

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publications (
    publication_key TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    notion_page_id TEXT,
    postmypost_publication_id TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    format TEXT,
    slot TEXT,
    account_id TEXT,
    scheduled_at TEXT,
    created_at TEXT NOT NULL,
    published_at TEXT,
    status TEXT,
    raw_status INTEGER,
    external_media_id TEXT,
    permalink TEXT,
    source TEXT NOT NULL DEFAULT 'postmypost',
    last_synced_at TEXT,
    publication_type INTEGER,
    extra_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_publications_object_id
    ON publications(object_id);
CREATE INDEX IF NOT EXISTS idx_publications_platform_format
    ON publications(platform, format);
CREATE INDEX IF NOT EXISTS idx_publications_page_slot
    ON publications(notion_page_id, slot);
"""


class PublicationLedgerError(RuntimeError):
    """Raised when ledger persistence fails after an external publication exists."""


@dataclass(frozen=True)
class PublicationRow:
    publication_key: str
    object_id: str
    notion_page_id: str | None
    postmypost_publication_id: str
    platform: str
    format: str | None
    slot: str | None
    account_id: str | None
    scheduled_at: str | None
    created_at: str
    published_at: str | None
    status: str | None
    raw_status: int | None
    external_media_id: str | None
    permalink: str | None
    source: str
    last_synced_at: str | None
    publication_type: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_db_path() -> Path:
    return package_root() / "data" / "publications.sqlite3"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_status(raw_status: int | None) -> str | None:
    """Map only statuses confirmed in local client/fixtures."""
    if raw_status is None:
        return None
    if int(raw_status) == RAW_STATUS_PUBLISHED:
        return "published"
    if int(raw_status) == RAW_STATUS_PENDING:
        return "pending"
    return None


def infer_format(
    *,
    platform: str,
    post_kind: str | None,
    upload_video: bool = False,
    publication_type: int | None = None,
) -> str | None:
    """Honest format inference from our write path + confirmed type constants."""
    network = (platform or "").lower()
    if network in {"twitter"}:
        network = "x"

    if post_kind in {"reel", "carousel", "video", "post"}:
        if post_kind == "video":
            return "reel" if network == "instagram" else "post"
        return post_kind

    if network == "instagram":
        if upload_video or publication_type == PUBLICATION_TYPE_REELS:
            return "reel"
        if publication_type == PUBLICATION_TYPE_POST:
            return "carousel"  # Agent 4 IG photo path uses carousel slot
        return None

    if network == "facebook":
        # Do not guess Facebook Reel unless our write path used REELS type.
        if publication_type == PUBLICATION_TYPE_REELS or upload_video:
            return "reel"
        if publication_type == PUBLICATION_TYPE_POST or publication_type is None:
            return "post"
        return None

    # Other publisher networks: store what we know without Agent 10 semantics.
    if publication_type == PUBLICATION_TYPE_REELS or upload_video:
        return "reel"
    if post_kind:
        return post_kind
    if publication_type == PUBLICATION_TYPE_POST:
        return "post"
    return None


def extract_external_media_id(payload: dict[str, Any], account_id: str | None = None) -> str | None:
    posts = payload.get("posts") or []
    wanted = str(account_id) if account_id is not None else None
    for post in posts:
        if not isinstance(post, dict):
            continue
        if wanted is not None and str(post.get("account_id")) != wanted:
            continue
        ext = post.get("external_id")
        if ext is None or ext == "" or ext == 0 or ext == "0":
            continue
        return str(ext)
    # Fallback: first non-empty external_id on any post
    for post in posts:
        if not isinstance(post, dict):
            continue
        ext = post.get("external_id")
        if ext is None or ext == "" or ext == 0 or ext == "0":
            continue
        return str(ext)
    return None


class PublicationLedger:
    """SQLite-backed append/idempotent publication history."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                ("version", str(SCHEMA_VERSION)),
            )

    def register_from_create(
        self,
        *,
        object_id: str,
        notion_page_id: str,
        postmypost_publication_id: str,
        platform: str,
        slot: str | None,
        account_id: str | int | None,
        scheduled_at: str | None,
        post_kind: str | None = None,
        upload_video: bool = False,
        publication_type: int | None = None,
        raw_status: int | None = RAW_STATUS_PENDING,
        source: str = "postmypost",
    ) -> PublicationRow:
        """Idempotent insert keyed by postmypost_publication_id. No network."""
        pmp_id = str(postmypost_publication_id).strip()
        if not pmp_id:
            raise PublicationLedgerError("postmypost_publication_id required")

        existing = self.get_by_postmypost_id(pmp_id)
        if existing is not None:
            return existing

        fmt = infer_format(
            platform=platform,
            post_kind=post_kind,
            upload_video=upload_video,
            publication_type=publication_type,
        )
        row = PublicationRow(
            publication_key=str(uuid.uuid4()),
            object_id=str(object_id or "").strip(),
            notion_page_id=notion_page_id or None,
            postmypost_publication_id=pmp_id,
            platform=str(platform).lower(),
            format=fmt,
            slot=slot,
            account_id=None if account_id is None else str(account_id),
            scheduled_at=scheduled_at,
            created_at=_now_iso(),
            published_at=None,
            status=normalize_status(raw_status),
            raw_status=None if raw_status is None else int(raw_status),
            external_media_id=None,
            permalink=None,
            source=source,
            last_synced_at=None,
            publication_type=publication_type,
        )
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO publications (
                        publication_key, object_id, notion_page_id,
                        postmypost_publication_id, platform, format, slot,
                        account_id, scheduled_at, created_at, published_at,
                        status, raw_status, external_media_id, permalink,
                        source, last_synced_at, publication_type, extra_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.publication_key,
                        row.object_id,
                        row.notion_page_id,
                        row.postmypost_publication_id,
                        row.platform,
                        row.format,
                        row.slot,
                        row.account_id,
                        row.scheduled_at,
                        row.created_at,
                        row.published_at,
                        row.status,
                        row.raw_status,
                        row.external_media_id,
                        row.permalink,
                        row.source,
                        row.last_synced_at,
                        row.publication_type,
                        None,
                    ),
                )
        except sqlite3.IntegrityError:
            # Race / duplicate PostMyPost id
            existing = self.get_by_postmypost_id(pmp_id)
            if existing is not None:
                return existing
            raise PublicationLedgerError(
                f"failed to register publication_id={pmp_id}"
            ) from None
        return row

    def enrich_from_get_payload(
        self,
        postmypost_publication_id: str,
        payload: dict[str, Any],
        *,
        permalink: str | None = None,
        platform: str | None = None,
    ) -> PublicationRow | None:
        """Update existing row from GET /publications/{id}. Never insert duplicates."""
        pmp_id = str(postmypost_publication_id).strip()
        row = self.get_by_postmypost_id(pmp_id)
        if row is None:
            logger.warning(
                "ledger enrichment skipped: unknown postmypost_publication_id=%s",
                pmp_id,
            )
            return None

        raw_status = payload.get("publication_status")
        raw_status_i = int(raw_status) if raw_status is not None else row.raw_status
        status = normalize_status(raw_status_i)

        account_id = row.account_id
        if account_id is None:
            accounts = payload.get("account_ids") or []
            if accounts:
                account_id = str(accounts[0])

        external_id = extract_external_media_id(payload, account_id)
        # Only accept social permalink (caller should pass validated URL)
        permalink_val = permalink if permalink else row.permalink

        # Do not invent published_at — PostMyPost GET fixtures do not expose a confirmed field.
        published_at = row.published_at

        # Optionally refine format from details[].publication_type when still null
        fmt = row.format
        pub_type = row.publication_type
        details = payload.get("details") or []
        if details and isinstance(details[0], dict) and details[0].get("publication_type") is not None:
            pub_type = int(details[0]["publication_type"])
            if fmt is None:
                fmt = infer_format(
                    platform=platform or row.platform,
                    post_kind=None,
                    publication_type=pub_type,
                )

        synced = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE publications SET
                    account_id = COALESCE(?, account_id),
                    status = ?,
                    raw_status = ?,
                    external_media_id = COALESCE(?, external_media_id),
                    permalink = COALESCE(?, permalink),
                    published_at = ?,
                    format = COALESCE(?, format),
                    publication_type = COALESCE(?, publication_type),
                    last_synced_at = ?
                WHERE postmypost_publication_id = ?
                """,
                (
                    account_id,
                    status,
                    raw_status_i,
                    external_id,
                    permalink_val,
                    published_at,
                    fmt,
                    pub_type,
                    synced,
                    pmp_id,
                ),
            )
        return self.get_by_postmypost_id(pmp_id)

    def get_by_postmypost_id(self, postmypost_publication_id: str) -> PublicationRow | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM publications WHERE postmypost_publication_id = ?",
                (str(postmypost_publication_id),),
            )
            row = cur.fetchone()
        return self._row_to_model(row) if row else None

    def get_by_key(self, publication_key: str) -> PublicationRow | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM publications WHERE publication_key = ?",
                (publication_key,),
            )
            row = cur.fetchone()
        return self._row_to_model(row) if row else None

    def list_by_object(
        self,
        object_id: str,
        *,
        platform: str | None = None,
        format: str | None = None,
    ) -> list[PublicationRow]:
        sql = "SELECT * FROM publications WHERE object_id = ?"
        params: list[Any] = [str(object_id)]
        if platform:
            sql += " AND platform = ?"
            params.append(platform.lower())
        if format:
            sql += " AND format = ?"
            params.append(format)
        sql += " ORDER BY created_at ASC, postmypost_publication_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_model(r) for r in rows]

    def get_latest_by_page_slot(
        self,
        notion_page_id: str,
        slot: str,
    ) -> PublicationRow | None:
        """Most recent publication for a Notion page + canonical slot key."""
        page_id = str(notion_page_id or "").strip()
        slot_id = str(slot or "").strip()
        if not page_id or not slot_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM publications
                WHERE notion_page_id = ? AND slot = ?
                ORDER BY created_at DESC, postmypost_publication_id DESC
                LIMIT 1
                """,
                (page_id, slot_id),
            ).fetchone()
        return self._row_to_model(row) if row else None

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0])

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> PublicationRow:
        return PublicationRow(
            publication_key=row["publication_key"],
            object_id=row["object_id"],
            notion_page_id=row["notion_page_id"],
            postmypost_publication_id=row["postmypost_publication_id"],
            platform=row["platform"],
            format=row["format"],
            slot=row["slot"],
            account_id=row["account_id"],
            scheduled_at=row["scheduled_at"],
            created_at=row["created_at"],
            published_at=row["published_at"],
            status=row["status"],
            raw_status=row["raw_status"],
            external_media_id=row["external_media_id"],
            permalink=row["permalink"],
            source=row["source"],
            last_synced_at=row["last_synced_at"],
            publication_type=row["publication_type"],
        )


def register_publication_in_ledger(
    *,
    object_id: str,
    notion_page_id: str,
    postmypost_publication_id: str,
    platform: str,
    slot: str | None,
    account_id: str | int | None,
    scheduled_at: str | None,
    post_kind: str | None = None,
    upload_video: bool = False,
    publication_type: int | None = None,
    raw_status: int | None = RAW_STATUS_PENDING,
    db_path: Path | None = None,
) -> PublicationRow:
    ledger = PublicationLedger(db_path)
    return ledger.register_from_create(
        object_id=object_id,
        notion_page_id=notion_page_id,
        postmypost_publication_id=postmypost_publication_id,
        platform=platform,
        slot=slot,
        account_id=account_id,
        scheduled_at=scheduled_at,
        post_kind=post_kind,
        upload_video=upload_video,
        publication_type=publication_type,
        raw_status=raw_status,
    )


def enrich_publication_in_ledger(
    postmypost_publication_id: str,
    payload: dict[str, Any],
    *,
    permalink: str | None = None,
    platform: str | None = None,
    db_path: Path | None = None,
) -> PublicationRow | None:
    ledger = PublicationLedger(db_path)
    return ledger.enrich_from_get_payload(
        postmypost_publication_id,
        payload,
        permalink=permalink,
        platform=platform,
    )


def reconcile_from_current_state(
    state: dict[str, Any],
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Import known current-pointer JSON entries into ledger (offline, no network)."""
    ledger = PublicationLedger(db_path)
    imported = 0
    skipped = 0
    errors: list[str] = []
    slots = state.get("slots") or {}
    if not isinstance(slots, dict):
        return {"imported": 0, "skipped": 0, "errors": ["invalid_state"]}

    for key, entry in slots.items():
        if not isinstance(entry, dict):
            skipped += 1
            continue
        pmp_id = entry.get("publication_id")
        object_id = entry.get("object_id")
        page_id = entry.get("page_id")
        platform = entry.get("platform")
        if not pmp_id or not object_id or not platform:
            skipped += 1
            errors.append(f"incomplete:{key}")
            continue
        try:
            ledger.register_from_create(
                object_id=str(object_id),
                notion_page_id=str(page_id or ""),
                postmypost_publication_id=str(pmp_id),
                platform=str(platform),
                slot=entry.get("slot_id") or entry.get("slot"),
                account_id=None,
                scheduled_at=entry.get("scheduled_time"),
                post_kind=entry.get("post_kind"),
                source="reconcile_current_state",
            )
            imported += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{key}:{exc}")
            skipped += 1
    return {"imported": imported, "skipped": skipped, "errors": errors, "total": ledger.count()}
