#!/usr/bin/env python3
"""Unified production status for Open Home multi-agent VPS (no secrets)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
AGENT6 = ROOT / "agent_6_qualifier"
sys.path.insert(0, str(AGENT6 / "src"))
sys.path.insert(0, str(AGENT6 / "scripts"))


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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _present(*names: str) -> bool:
    return any(bool(str(os.getenv(n) or "").strip()) for n in names)


def _probe(url: str, timeout: float = 2.5) -> bool:
    try:
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return int(getattr(resp, "status", 200) or 200) == 200
    except (URLError, OSError, ValueError):
        return False


def _systemctl_active(unit: str) -> str:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return (r.stdout or r.stderr or "unknown").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "n/a"


def _mem_disk() -> tuple[str, str]:
    mem = "unknown"
    disk = "unknown"
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            info = {}
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k] = v.strip()
            total = int(info.get("MemTotal", "0 kB").split()[0])
            avail = int(info.get("MemAvailable", "0 kB").split()[0])
            used_pct = int(round(100 * (1 - (avail / total)))) if total else 0
            mem = f"{used_pct}% used ({avail // 1024}MiB avail / {total // 1024}MiB)"
    except OSError:
        pass
    usage = shutil.disk_usage("/")
    disk = f"{int(usage.used * 100 / usage.total)}% used ({usage.free // (1024**3)}GiB free)"
    return mem, disk


def main() -> int:
    _load_dotenv(ROOT / ".env")
    _load_dotenv(AGENT6 / ".env")
    _load_dotenv(Path(os.getenv("OPENHOME_ENV_FILE") or "/opt/openhome/.env"))

    from social_auth_status import (  # type: ignore
        airbnb_status,
        facebook_status,
        telegram_status,
        wazzup_status,
    )

    try:
        from agent7_envoy.amo_chat.dns_readiness import check_dns

        dns_ready = bool(check_dns().ready)
    except Exception:
        dns_ready = False

    api_ok = _probe("http://127.0.0.1:8000/health")
    nginx = _systemctl_active("nginx")
    api_svc = _systemctl_active("openhome-api")
    amo_timer = _systemctl_active("openhome-amo-task.timer")

    amo = "BLOCKED"
    if _present("AMO_SUBDOMAIN", "AMO_ACCESS_TOKEN"):
        try:
            from amo_chat_account_info import fetch_account_info

            info = fetch_account_info()
            amo = "READY" if getattr(info, "ready", False) else "BLOCKED"
        except Exception:
            amo = "BLOCKED"

    notion = "READY" if _present("NOTION_TOKEN", "NOTION_API_KEY") and _present(
        "NOTION_DATABASE_ID", "NOTION_DB_ID"
    ) else "BLOCKED"

    r2 = (
        "READY"
        if _present("CLOUDFLARE_ACCESS_KEY_ID")
        and _present("CLOUDFLARE_SECRET_ACCESS_KEY")
        and _present("CLOUDFLARE_BUCKET")
        else "BLOCKED"
    )

    models = "READY" if _present("GEMINI_API_KEY") else "BLOCKED"

    stores_ok = Path("/opt/openhome/runtime/stores").exists() or Path(
        "/opt/openhome/runtime"
    ).exists()
    persistence = "READY" if stores_ok else "BLOCKED"

    fb = facebook_status()
    ab = airbnb_status()
    tg = telegram_status()
    wz = wazzup_status()

    live_flags = {
        "AGENT7_FB": _env_bool("AGENT7_FACEBOOK_MESSENGER_ENABLED"),
        "AGENT7_AIRBNB": _env_bool("AGENT7_AIRBNB_MESSAGES_ENABLED"),
        "AGENT7_LIVE": _env_bool("AGENT7_LIVE_OUTREACH_ENABLED"),
        "AMO_WEBHOOK": _env_bool("AMO_CHAT_WEBHOOK_ENABLED"),
        "AMO_MIRROR": _env_bool("AMO_CHAT_MIRROR_LIVE"),
        "AMO_CONNECT": _env_bool("AMO_CHAT_CONNECT_LIVE"),
        "WAZZUP_SEND": _env_bool("WAZZUP_SEND_ENABLED"),
        "WAZZUP_WEBHOOK": _env_bool("WAZZUP_WEBHOOK_ENABLED"),
        "PUBLISHER_SOCIAL": _env_bool("PUBLISHER_SOCIAL_ENABLED"),
    }

    agents = {
        "1": "BLOCKED — bots installed but not started (user approval)",
        "2": "BLOCKED — registrar/chain not auto-started",
        "3": "BLOCKED — depends on chain watcher OFF",
        "4": "BLOCKED — publisher live OFF (PUBLISHER_SOCIAL_ENABLED=false)",
        "5": "BLOCKED — no dedicated systemd unit in current map",
        "6": "BLOCKED — qualifier userbot not started (WA/TG live gated)",
        "7": f"PARTIAL — core libs READY; FB={fb}; Airbnb={ab}; source-native LIVE OFF",
        "8": "BLOCKED — calendar/check needs Playwright job + approval",
        "9": "BLOCKED — connector service not enabled",
        "10": "BLOCKED — Meta write gates OFF; AGENT10_LLM_API_KEY missing",
    }

    mem, disk = _mem_disk()
    version = Path("/opt/openhome/DEPLOYED_VERSION")
    ver = version.read_text(encoding="utf-8").strip() if version.exists() else "unknown"

    print("SERVER")
    print(f"{socket.gethostname()} 72.60.108.152")
    print()
    print("API")
    print("RUNNING" if api_ok and api_svc == "active" else f"ISSUE api={api_ok} svc={api_svc}")
    print()
    print("NGINX")
    print(nginx.upper() if nginx == "active" else nginx)
    print()
    print("AGENTS")
    for k, v in agents.items():
        print(f"AGENT {k}: {v}")
    print()
    print("AMO")
    print(amo)
    print()
    print("WAZZUP")
    print(wz)
    print()
    print("NOTION")
    print(notion)
    print()
    print("R2")
    print(r2)
    print()
    print("FACEBOOK AUTH")
    print(fb)
    print()
    print("AIRBNB AUTH")
    print(ab)
    print()
    print("TELEGRAM")
    print(tg)
    print()
    print("DATABASE/STORES")
    print(persistence)
    print()
    print("WORKERS")
    print(f"openhome-api={api_svc} amo-task.timer={amo_timer}")
    print()
    print("LIVE FLAGS")
    for k, v in live_flags.items():
        print(f"{k}={'ON' if v else 'OFF'}")
    print()
    print("DNS/HTTPS")
    print("DNS:", "READY" if dns_ready else "WAITING")
    print("HTTPS:", "READY" if _probe("https://api.open-home.online/health", 3.0) else "WAITING")
    print()
    print("MODEL PROVIDERS")
    print(models)
    print()
    print("RESOURCES")
    print(f"RAM: {mem}")
    print(f"DISK: {disk}")
    print()
    print("DEPLOYED_VERSION")
    print(ver)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
