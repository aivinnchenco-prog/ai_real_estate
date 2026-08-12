#!/usr/bin/env python3
"""Read-only status for local E2E launcher. Never prints secrets."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from local_e2e_lib import (  # noqa: E402
    AGENT6_ENV,
    AGENT6_ROOT,
    AIRBNB_MAIN,
    ASSISTANT_MEDIA,
    CHAIN_RUNNER,
    FB_BOT,
    REPO_ROOT,
    RUNTIME_DIR,
    STATUS_PATH,
    WEBHOOK_HEALTH,
    RuntimeState,
    local_webhook_ready,
    load_dotenv_file,
    montage_lock_held,
    pid_alive,
    probe_url,
    property_entrypoints,
    read_env_key,
    redact_for_log,
    save_json,
    utc_now,
)


def _flag(path: Path, key: str) -> bool:
    raw = read_env_key(path, key, os.getenv(key, "false"))
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _armed(path: Path, key: str) -> str:
    return "ARMED" if _flag(path, key) else "OFF"


def _env_ready(*keys: str) -> str:
    for key in keys:
        val = os.getenv(key) or read_env_key(AGENT6_ENV, key) or read_env_key(
            REPO_ROOT / ".env", key
        )
        if not val.strip():
            return "FAIL"
    return "READY"


def main() -> int:
    load_dotenv_file(REPO_ROOT / ".env")
    load_dotenv_file(AGENT6_ENV, override=True)
    load_dotenv_file(ASSISTANT_MEDIA / ".env.real-estate")
    load_dotenv_file(ASSISTANT_MEDIA / ".env")

    state = RuntimeState.load()
    lines: list[str] = []

    def show(label: str, value: str) -> None:
        lines.append(f"{label}: {value}")

    # Agent1 input
    a1_pid = state.pids.get("agent1_airbnb") or state.pids.get("agent1_fb")
    if a1_pid and pid_alive(a1_pid):
        show("Agent1 input", "READY")
    elif AIRBNB_MAIN.exists() or FB_BOT.exists():
        show("Agent1 input", "OFF")
    else:
        show("Agent1 input", "FAIL")

    show("Agent2", "READY" if (REPO_ROOT / "agent_2_registrar").is_dir() else "FAIL")

    cw = state.pids.get("chain_watcher")
    if cw and pid_alive(cw) and CHAIN_RUNNER.exists():
        show("chain watcher", "RUNNING")
    else:
        show("chain watcher", "FAIL")

    show(
        "Agent3",
        "READY" if (REPO_ROOT / "agent_3_director").is_dir() else "FAIL",
    )
    show(
        "Agent4 PostMyPost",
        "READY" if (REPO_ROOT / "agent_4_publisher").is_dir() else "FAIL",
    )

    pmp = _env_ready("POSTMYPOST_API_KEY", "POSTMYPOST_TOKEN", "POSTMYPOST_ACCESS_TOKEN")
    # Any one of common PostMyPost keys
    pmp_ok = any(
        (os.getenv(k) or read_env_key(REPO_ROOT / ".env", k) or read_env_key(AGENT6_ENV, k) or read_env_key(REPO_ROOT / "agent_4_publisher" / ".env", k)).strip()
        for k in (
            "POSTMYPOST_API_KEY",
            "POSTMYPOST_TOKEN",
            "POSTMYPOST_ACCESS_TOKEN",
            "POSTMYPOST_API_TOKEN",
            "PMP_API_KEY",
        )
    )
    show("PostMyPost auth", "READY" if pmp_ok else "FAIL")

    notion_ok = any(
        (os.getenv(k) or read_env_key(REPO_ROOT / ".env", k) or read_env_key(AGENT6_ENV, k)).strip()
        for k in ("NOTION_TOKEN", "NOTION_API_KEY")
    ) and any(
        (os.getenv(k) or read_env_key(REPO_ROOT / ".env", k) or read_env_key(AGENT6_ENV, k)).strip()
        for k in ("NOTION_DATABASE_ID", "NOTION_DB_ID")
    )
    show("Notion", "READY" if notion_ok else "FAIL")

    r2_ok = any(
        (
            os.getenv(k)
            or read_env_key(REPO_ROOT / ".env", k)
            or read_env_key(AGENT6_ENV, k)
            or read_env_key(ASSISTANT_MEDIA / ".env", k)
            or read_env_key(ASSISTANT_MEDIA / ".env.real-estate", k)
        ).strip()
        for k in (
            "R2_SECRET_ACCESS_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "CLOUDFLARE_R2_SECRET",
            "CLOUDFLARE_SECRET_ACCESS_KEY",
            "R2_ACCESS_KEY_ID",
            "CF_R2_ACCESS_KEY_ID",
            "CLOUDFLARE_ACCESS_KEY_ID",
        )
    ) and any(
        (
            os.getenv(k)
            or read_env_key(REPO_ROOT / ".env", k)
            or read_env_key(ASSISTANT_MEDIA / ".env.real-estate", k)
        ).strip()
        for k in (
            "R2_PUBLIC_BASE",
            "CLOUDFLARE_PUBLIC_BASE_URL",
            "R2_PUBLIC_BASE_URL",
        )
    )
    show("R2", "READY" if r2_ok else "FAIL")

    amo_ok = any(
        (os.getenv(k) or read_env_key(REPO_ROOT / ".env", k) or read_env_key(AGENT6_ENV, k)).strip()
        for k in ("AMO_ACCESS_TOKEN", "AMOCRM_ACCESS_TOKEN", "AMO_TOKEN")
    )
    show("amoCRM", "READY" if amo_ok else "FAIL")

    # Wazzup API (no key print)
    wazzup_key = (
        os.getenv("WAZZUP_API_KEY") or read_env_key(AGENT6_ENV, "WAZZUP_API_KEY")
    ).strip()
    show("Wazzup", "READY" if wazzup_key else "FAIL")

    wh_pid = state.pids.get("wazzup_webhook")
    local_wh = "READY" if local_webhook_ready() else "FAIL"
    if wh_pid and not pid_alive(wh_pid) and local_wh == "READY":
        local_wh = "READY"  # external healthy listener ok
    show("Webhook local", local_wh)

    tun = state.tunnel or {}
    public_base = str(tun.get("public_base") or "").rstrip("/")
    cf_pid = state.pids.get("cloudflared")
    cf_svc = (state.services or {}).get("cloudflared") or {}
    if cf_svc.get("status") == "OFF" or tun.get("note") == "tunnel skipped":
        show("Tunnel", "OFF")
        show("Wazzup public webhook", "OFF")
    elif cf_pid and pid_alive(cf_pid) and public_base:
        from local_e2e_lib import wait_public_webhook_ready

        show("Tunnel", "READY")
        show(
            "Wazzup public webhook",
            "READY" if wait_public_webhook_ready(public_base, timeout_s=2.0) else "FAIL",
        )
    elif public_base:
        show("Tunnel", "FAIL")
        show("Wazzup public webhook", "FAIL")
    else:
        show("Tunnel", "FAIL")
        show("Wazzup public webhook", "FAIL")

    show("Agent6", "READY" if (AGENT6_ROOT / "src" / "agent6_qualifier").is_dir() else "FAIL")

    confirmed = _flag(AGENT6_ENV, "WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF")
    a7_live = _flag(AGENT6_ENV, "AGENT7_LIVE_OUTREACH_ENABLED")
    if a7_live:
        show("Agent7", "ARMED")
    else:
        show("Agent7", "GATED")

    show(
        "Agent8",
        "READY"
        if (REPO_ROOT / "agent_8_notary").is_dir()
        or (AGENT6_ROOT / "src" / "agent8").is_dir()
        else "FAIL",
    )

    show("Phone social", "OFF")
    show("Agent5", "OFF")
    show("Agent9", "OFF")
    show("Agent10", "OFF")
    show("Meta Ads", "OFF")

    show("WA SEND", _armed(AGENT6_ENV, "WAZZUP_SEND_ENABLED"))
    show("WA AUTO_REPLY", _armed(AGENT6_ENV, "WAZZUP_AUTO_REPLY_ENABLED"))
    show("OWNER OUTREACH", _armed(AGENT6_ENV, "AGENT7_LIVE_OUTREACH_ENABLED"))

    if not confirmed:
        lines.append("")
        lines.append("WA LIVE SEND: BLOCKED")
        lines.append("REASON: EXTERNAL AUTORESPONSE NOT CONFIRMED OFF")

    held, holder = montage_lock_held()
    lines.append("")
    lines.append(
        f"montage flock: {'HELD (' + holder + ')' if held else 'free (text file ignored)'}"
    )
    lines.append("")
    lines.append("PROPERTY INPUT:")
    for name, cmd in property_entrypoints().items():
        lines.append(f"  {name}: {cmd}")

    text = "\n".join(lines)
    print(redact_for_log(text))
    save_json(
        STATUS_PATH,
        {
            "updated_at": utc_now(),
            "lines": lines,
            "runtime": str(RUNTIME_DIR),
            "confirmed_autoresponse_off": confirmed,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
