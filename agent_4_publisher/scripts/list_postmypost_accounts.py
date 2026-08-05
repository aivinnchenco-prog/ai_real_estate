#!/usr/bin/env python3
"""List PostMyPost projects and connected accounts for publisher.json mapping."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from postmypost_client import (  # noqa: E402
    channel_code_by_id,
    list_accounts,
    list_projects,
    postmypost_cfg,
)
from publish_pipeline import load_config, load_dotenv  # noqa: E402


def main() -> int:
    load_dotenv()
    config = load_config()
    cfg = postmypost_cfg(config)
    project_id = cfg.get("project_id")

    print(json.dumps({"postmypost": cfg}, ensure_ascii=False, indent=2))
    print()

    projects = list_projects(config)
    print("Projects:")
    for project in projects:
        marker = " <-- configured" if project.get("id") == project_id else ""
        print(f"  - {project.get('id')}: {project.get('name')}{marker}")

    if not project_id:
        print("\nSet postmypost.project_id in config/publisher.json", file=sys.stderr)
        return 1

    codes = channel_code_by_id(config)
    accounts = list_accounts(int(project_id), config)
    print(f"\nAccounts for project {project_id}:")
    if not accounts:
        print("  (none — подключите соцсети в PostMyPost UI)")
    for account in accounts:
        channel_id = account.get("chanel_id") or account.get("channel_id")
        code = codes.get(int(channel_id or 0), "?")
        status = "ok" if int(account.get("connection_status") or 0) == 1 else "offline"
        print(
            f"  - id={account.get('id')} channel={code} "
            f"name={account.get('name')!r} status={status}"
        )

    suggested = {}
    for account in accounts:
        if int(account.get("connection_status") or 0) != 1:
            continue
        channel_id = account.get("chanel_id") or account.get("channel_id")
        code = codes.get(int(channel_id or 0), "")
        if not code:
            continue
        suggested.setdefault(code, []).append(int(account["id"]))
    if suggested:
        print("\nSuggested platform_accounts snippet:")
        print(json.dumps({"platform_accounts": suggested}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
