#!/usr/bin/env python3
"""Fetch safe amoCRM account ids for custom chat channel registration.

Uses existing AmoClient (AMO_SUBDOMAIN + AMO_ACCESS_TOKEN).
Never prints tokens/secrets. Does not generate or rewrite credentials.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[0]
sys.path.insert(0, str(ROOT / "src"))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def fetch_account_info():
    from agent6_qualifier.amo import AmoClient
    from agent7_envoy.amo_chat.registration import parse_account_payload, require_amojo_id

    client = AmoClient()
    payload = client._req("GET", "/account?with=amojo_id")
    info = parse_account_payload(payload)
    require_amojo_id(info)
    return info


def main() -> int:
    _load_dotenv(REPO / ".env")
    _load_dotenv(ROOT / ".env")
    _load_dotenv(Path(os.getenv("OPENHOME_ENV_FILE") or "/opt/openhome/.env"))

    from agent7_envoy.amo_chat.registration import resolve_client_uuid

    try:
        info = fetch_account_info()
    except Exception as exc:
        print("AMO ACCOUNT INFO")
        print()
        print("STATUS:")
        print("FAIL")
        print()
        print(f"ERROR: {type(exc).__name__}")
        return 1

    uuid_check = resolve_client_uuid()
    status = "READY" if info.ready and uuid_check.match else "FAIL"

    print("AMO ACCOUNT INFO")
    print()
    print("ACCOUNT_ID:")
    print(info.account_id)
    print()
    print("AMOJO_ID:")
    print(info.amojo_id)
    print()
    print("CLIENT_UUID:")
    print(uuid_check.current)
    print()
    print("SUBDOMAIN:")
    print(info.subdomain)
    print()
    print("STATUS:")
    print(status)
    return 0 if status == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
