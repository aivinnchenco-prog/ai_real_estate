"""Read-only Notion path diagnostic — prints fingerprints, not secrets."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path


def load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def fp(val: str) -> str:
    if not val:
        return "(empty)"
    return hashlib.sha256(val.encode()).hexdigest()[:12]


def notion_get(token: str, path: str, version: str = "2022-06-28") -> tuple[int, str]:
    url = f"https://api.notion.com/v1{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": version,
            "Content-Type": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


def main() -> None:
    canon = load_dotenv(Path("/opt/openhome/.env"))
    app = load_dotenv(Path("/opt/openhome/app/.env"))
    app_before = load_dotenv(
        Path("/opt/openhome/backups/availability-env-migrate-20260814T155616Z/app.env.before")
    )

    source = canon.get("AVAILABILITY_SOURCE_NOTION_DATABASE_ID") or canon.get("NOTION_DB_ID") or ""
    target = canon.get("AVAILABILITY_TARGET_NOTION_DATABASE_ID", "")
    target_ds = canon.get("AVAILABILITY_TARGET_NOTION_DATA_SOURCE_ID", "")

    # Known IDs from dev history (for endpoint comparison only)
    known_targets = {
        "config_current": target,
        "app_before": app_before.get("AVAILABILITY_TARGET_NOTION_DATABASE_ID", ""),
        "dev_v3_active": "3bc2c251-5061-8126-9919-cdff45db0666",
        "dev_v1_trash": "3bc2c251-5061-8125-bf98-dbe235164c4c",
        "dev_v2_deleted": "3bc2c251-5061-81a0-8d29-de425d07f100",
    }

    print("CONFIG_IDS")
    print(f"  source_db={source}")
    print(f"  target_db_config={target}")
    print(f"  target_ds_config={target_ds}")

    token_vars = [
        ("NOTION_DOCS_API_KEY_app", app.get("NOTION_DOCS_API_KEY")),
        ("NOTION_DOCS_API_KEY_before", app_before.get("NOTION_DOCS_API_KEY")),
        ("NOTION_API_KEY_canon", canon.get("NOTION_API_KEY")),
        ("NOTION_API_KEY_app", app.get("NOTION_API_KEY")),
        ("NOTION_TOKEN_canon", canon.get("NOTION_TOKEN")),
    ]
    unique: dict[str, str] = {}
    for name, val in token_vars:
        if not val:
            print(f"TOKEN {name}: unset")
            continue
        f = fp(val)
        src = "canonical" if "canon" in name else ("app.before" if "before" in name else "app/.env")
        if val in unique:
            print(f"TOKEN {name}: fp={f} source={src} SAME_AS={unique[val]}")
        else:
            unique[val] = name
            print(f"TOKEN {name}: fp={f} source={src}")

    for val, label in unique.items():
        code, body = notion_get(val, "/users/me")
        try:
            data = json.loads(body)
            who = data.get("name") or data.get("bot", {}).get("owner", {})
        except Exception:
            who = body[:60]
        print(f"INTEGRATION {label}: HTTP {code} identity={who}")

    from availability_service.app.config import (
        canonical_env_source,
        load_config,
        load_project_env,
        loaded_env_sources,
    )

    load_project_env()
    cfg = load_config()
    print("LOAD_CONFIG")
    print(f"  env_sources={loaded_env_sources()}")
    print(f"  canonical={canonical_env_source()}")
    print(f"  token_fp={fp(cfg.notion_api_key)}")
    print(f"  target_db={cfg.target_database_id}")
    print(f"  target_ds={cfg.target_data_source_id}")

    t = cfg.notion_api_key
    docs = app_before.get("NOTION_DOCS_API_KEY") or app.get("NOTION_DOCS_API_KEY")

    def test_token(label: str, token: str, db_id: str) -> None:
        if not token or not db_id:
            return
        for ep, path, ver in [
            ("db_2022", f"/databases/{db_id}", "2022-06-28"),
            ("db_2025", f"/databases/{db_id}", "2025-09-03"),
        ]:
            code, body = notion_get(token, path, ver)
            try:
                data = json.loads(body)
                title = "".join(x.get("plain_text", "") for x in (data.get("title") or []))
                err = data.get("code") or title[:40]
            except Exception:
                err = body[:50]
            print(f"  {label} {ep} {db_id[:8]}... HTTP {code} {err}")

    print("TARGET_DB_TESTS load_config_token:")
    for label, db_id in known_targets.items():
        if db_id:
            test_token(label, t, db_id)

    if docs and fp(docs) != fp(t):
        print("TARGET_DB_TESTS NOTION_DOCS_API_KEY:")
        for label, db_id in known_targets.items():
            if db_id:
                test_token(label, docs, db_id)

    if target_ds:
        code, body = notion_get(t, f"/data_sources/{target_ds}", "2025-09-03")
        try:
            err = json.loads(body).get("code") or json.loads(body).get("name", "")[:40]
        except Exception:
            err = body[:50]
        print(f"DATA_SOURCE config_ds load_config_token: HTTP {code} {err}")
        if docs and fp(docs) != fp(t):
            code, body = notion_get(docs, f"/data_sources/{target_ds}", "2025-09-03")
            try:
                err = json.loads(body).get("code") or ""
            except Exception:
                err = body[:40]
            print(f"DATA_SOURCE config_ds NOTION_DOCS: HTTP {code} {err}")


if __name__ == "__main__":
    main()
