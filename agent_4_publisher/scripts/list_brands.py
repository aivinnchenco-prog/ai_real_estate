#!/usr/bin/env python3
"""List Metricool brand profile — copy userId, blogId, timezone to .env"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_dotenv() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in (".env", ".env.local"):
        env_path = root / name
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())
        break


def main() -> int:
    load_dotenv()
    token = os.environ.get("METRICOOL_USER_TOKEN") or os.environ.get("METRICOOL_API_TOKEN")
    user_id = os.environ.get("METRICOOL_USER_ID")
    blog_id = os.environ.get("METRICOOL_BLOG_ID")
    if not token:
        print("Set METRICOOL_USER_TOKEN in .env", file=sys.stderr)
        return 1
    if not user_id or not blog_id:
        print("Set METRICOOL_USER_ID and METRICOOL_BLOG_ID in .env", file=sys.stderr)
        print("Find them in the Metricool URL when viewing your brand dashboard.", file=sys.stderr)
        return 1

    url = (
        "https://app.metricool.com/api/admin/simpleProfiles?"
        + urllib.parse.urlencode({"userId": user_id, "blogId": blog_id})
    )
    req = urllib.request.Request(
        url,
        headers={
            "X-Mc-Auth": token,
            "User-Agent": "real-estate-agent6-publisher/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Metricool API error {e.code}: {e.read().decode()}", file=sys.stderr)
        return 1

    print(f"userId:  {user_id}")
    print(f"blogId:  {blog_id}")
    if isinstance(data, list):
        for item in data:
            label = item.get("label") or item.get("name") or item.get("network") or "?"
            print(f"  network: {label}")
    elif isinstance(data, dict):
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(data)

    print("\nDocs: https://app.metricool.com/resources/apidocs/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
