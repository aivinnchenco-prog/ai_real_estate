#!/usr/bin/env python3
"""Reconcile amoCRM Contact field «Тип контакта» + managed tags.

Default: DRY RUN (zero writes).
Apply mode: schema/tags only — never updates contacts/leads/pipelines.

Examples:
  python3 scripts/amocrm_reconcile_contact_role_schema.py --dry-run
  python3 scripts/amocrm_reconcile_contact_role_schema.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "contact_role" / "src"))
sys.path.insert(0, str(ROOT / "agent_6_qualifier" / "src"))

for env_path in (ROOT / ".env", ROOT / "agent_6_qualifier" / ".env"):
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile amoCRM contact role schema (field + managed tags)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create missing field/enums/tags only (no contact role writes)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only (default when --apply is not set)",
    )
    parser.add_argument(
        "--write-cache",
        action="store_true",
        help="Write resolved IDs to contact_role/data/amocrm_contact_role_schema.json",
    )
    args = parser.parse_args()
    if args.apply and args.dry_run:
        print("Choose either --dry-run or --apply, not both")
        return 2
    apply = bool(args.apply)

    if not (os.getenv("AMO_ACCESS_TOKEN") or "").strip():
        print("AMOCRM API: FAILED — AMO_ACCESS_TOKEN missing")
        return 2
    if not (os.getenv("AMO_SUBDOMAIN") or "").strip():
        print("AMOCRM API: FAILED — AMO_SUBDOMAIN missing")
        return 2

    from agent6_qualifier.amo import AmoClient
    from contact_role.schema_reconcile import (
        CODE_AMBIGUOUS,
        CODE_OK,
        CODE_TYPE_CONFLICT,
        ContactRoleSchemaReconciler,
        write_schema_cache,
    )

    try:
        amo = AmoClient()
        # connectivity probe
        account = amo.get_account()
        print(
            "AMOCRM API: CONNECTED "
            f"(account_id={account.get('id')}, name={account.get('name')!r})"
        )
    except Exception as exc:
        print(f"AMOCRM API: FAILED — {type(exc).__name__}: {exc}")
        return 2

    report = ContactRoleSchemaReconciler(amo).reconcile(apply=apply)
    print()
    print(report.to_text())

    if report.code == CODE_OK and (apply or args.write_cache):
        # Cache after successful apply, or when explicitly requested after dry-run verify read
        if apply or (args.write_cache and report.field_id):
            path = write_schema_cache(report)
            print(f"\nschema cache: {path}")

    if report.code == CODE_TYPE_CONFLICT:
        print("\nSTOP: AMO_CONTACT_ROLE_FIELD_TYPE_CONFLICT")
        return 3
    if report.code == CODE_AMBIGUOUS:
        print("\nSTOP: AMO_CONTACT_ROLE_FIELD_AMBIGUOUS")
        return 4
    if report.code != CODE_OK:
        return 1

    print("\ncontacts_updated=0 (schema/tags only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
