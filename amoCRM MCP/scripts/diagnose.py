#!/usr/bin/env python3
"""Safe read-only amoCRM connectivity diagnostic."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from amocrm_mcp.amo_client import ALLOWED_METHODS, ReadOnlyAmoClient
from amocrm_mcp.config import Settings


def main() -> int:
    settings = Settings.from_env()
    if not settings.amo_configured():
        print("amoCRM connection: MISSING_CREDENTIALS")
        print("Set AMO_MCP_SUBDOMAIN and AMO_MCP_ACCESS_TOKEN in .env")
        return 1
    client = ReadOnlyAmoClient(settings.amo_subdomain, settings.amo_access_token)
    account = client.get_account(with_params="task_types")
    pipelines = client.list_pipelines()
    users = client.list_users(limit=50)
    lead_fields = client.list_lead_fields()
    contact_fields = client.list_contact_fields()
    print("amoCRM connection: OK")
    print(f"account: {account.get('name')} ({account.get('subdomain')})")
    print(f"pipelines: {len(pipelines)}")
    print(f"users: {len(users)}")
    print(f"lead fields: {len(lead_fields)}")
    print(f"contact fields: {len(contact_fields)}")
    print(f"read-only guard: ACTIVE ({','.join(sorted(ALLOWED_METHODS))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
