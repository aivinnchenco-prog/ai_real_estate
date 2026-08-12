#!/usr/bin/env python3
"""Reconcile current PostMyPost slot JSON → canonical Publication Ledger (offline).

Does NOT call PostMyPost network APIs.
Skips incomplete entries that cannot be linked honestly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from postmypost_publication_state import load_state, state_path  # noqa: E402
from publication_ledger import default_db_path, reconcile_from_current_state  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile current-state JSON into publication ledger")
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Path to postmypost_publications.json (default: Agent 4 data path)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to publications.sqlite3 (default: Agent 4 data path)",
    )
    args = parser.parse_args()

    path = args.state or state_path()
    if not path.exists():
        print(json.dumps({"ok": False, "error": f"state_not_found:{path}"}, ensure_ascii=False))
        return 1

    state = load_state(path)
    result = reconcile_from_current_state(state, db_path=args.db or default_db_path())
    result["state_path"] = str(path)
    result["db_path"] = str(args.db or default_db_path())
    result["ok"] = True
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
