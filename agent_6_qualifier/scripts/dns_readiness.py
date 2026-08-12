#!/usr/bin/env python3
"""Read-only DNS readiness for api.open-home.online."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent7_envoy.amo_chat.dns_readiness import (  # noqa: E402
    EXPECTED_IP,
    PRODUCTION_DOMAIN,
    check_dns,
)


def main() -> int:
    result = check_dns()
    print("DNS READINESS")
    print()
    print("DOMAIN:")
    print(PRODUCTION_DOMAIN)
    print()
    print("EXPECTED IP:")
    print(EXPECTED_IP)
    print()
    print("RESOLVED:")
    print(", ".join(result.resolved_ips) if result.resolved_ips else "NONE")
    print()
    print("STATUS:")
    print(result.status)
    if result.detail:
        print()
        print("DETAIL:")
        print(result.detail)
    return 0 if result.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
