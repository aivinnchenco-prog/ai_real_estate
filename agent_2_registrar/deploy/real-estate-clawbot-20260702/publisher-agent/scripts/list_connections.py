#!/usr/bin/env python3
"""List Publora platform connections — copy platformId values to .env"""

import json
import os
import sys
import urllib.request
from pathlib import Path


def load_dotenv() -> None:
    root = Path(__file__).resolve().parents[3]
    for name in (".env", root / "_import" / "assistant-media" / ".env.real-estate"):
        env_path = Path(name) if isinstance(name, str) else name
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    load_dotenv()
    key = os.environ.get("PUBLORA_API_KEY")
    if not key:
        print("Set PUBLORA_API_KEY in .env", file=sys.stderr)
        return 1

    req = urllib.request.Request(
        "https://api.publora.com/api/v1/platform-connections",
        headers={"x-publora-key": key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    for conn in data.get("connections", []):
        pid = conn.get("platformId", "")
        platform = pid.split("-")[0] if pid else "?"
        print(f"{platform:12} {pid:40} @{conn.get('username', '')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
