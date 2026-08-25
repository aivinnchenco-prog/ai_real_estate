"""Remove an object from availability SQLite rotation (manual orphan cleanup)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import AvailabilityConfig, load_config
from .production_scheduler import backup_sqlite
from .repository import AvailabilityRepository


@dataclass
class RemoveObjectResult:
    object_id: str = ""
    existed: bool = False
    deleted: dict[str, int] = field(default_factory=dict)
    backup_path: str = ""
    dry_run: bool = False


def run_remove_object(
    object_id: str,
    *,
    confirm: bool = False,
    dry_run: bool = False,
    config: AvailabilityConfig | None = None,
) -> RemoveObjectResult:
    oid = (object_id or "").strip()
    if not oid:
        raise SystemExit("REFUSED: --object-id is required")
    if not dry_run and not confirm:
        raise SystemExit("REFUSED: remove-object requires --confirm (or --dry-run)")

    config = config or load_config()
    result = RemoveObjectResult(object_id=oid, dry_run=dry_run)
    repo = AvailabilityRepository(config.sqlite_path)
    try:
        state = repo.get_object(oid)
        result.existed = state is not None
        if not state:
            return result

        if dry_run:
            result.deleted = {
                "availability_objects": 1,
                "availability_calendar_days": repo.count_calendar_days(oid),
                "availability_months": repo.count_monthly_rows(oid),
                "refresh_jobs": "pending",
            }
            return result

        backup = backup_sqlite(config.sqlite_path)
        result.backup_path = str(backup)
        result.deleted = repo.remove_object(oid)
    finally:
        repo.close()
    return result


def print_remove_object_report(result: RemoveObjectResult) -> None:
    print("\n== REMOVE OBJECT (availability SQLite) ==")
    print(f"  object_id: {result.object_id}")
    print(f"  existed: {result.existed}")
    print(f"  dry_run: {result.dry_run}")
    if result.backup_path:
        print(f"  backup: {result.backup_path}")
    if result.deleted:
        print(f"  deleted_rows: {result.deleted}")
    if result.dry_run and result.existed:
        print("  (dry-run — no changes written)")
    elif result.existed:
        print("  OK — object removed from rotation")
