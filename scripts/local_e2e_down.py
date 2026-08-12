#!/usr/bin/env python3
"""Stop local E2E child processes started by local_e2e_up.py.

Forces live flags OFF. Keeps persistent data / queues / CRM untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from local_e2e_lib import (  # noqa: E402
    AGENT6_ENV,
    PIDS_PATH,
    REPO_ROOT,
    RUNTIME_DIR,
    STATUS_PATH,
    TUNNEL_PATH,
    RuntimeState,
    force_live_flags_off,
    save_json,
    stop_pid,
    utc_now,
)


def main() -> int:
    state = RuntimeState.load()
    stopped: list[str] = []
    for name, pid in list(state.pids.items()):
        stop_pid(pid, name=name)
        stopped.append(f"{name}:{pid}")
        state.pids.pop(name, None)

    force_live_flags_off(env_path=AGENT6_ENV)

    state.services["stopped_at"] = utc_now()
    state.services["stopped"] = stopped
    for key in (
        "wazzup_webhook",
        "chain_watcher",
        "cloudflared",
        "agent1_input",
    ):
        if key in state.services:
            state.services[key] = {
                **(state.services.get(key) or {}),
                "status": "STOPPED",
            }
    state.tunnel["stopped_at"] = utc_now()
    state.save()
    save_json(
        STATUS_PATH,
        {
            "updated_at": utc_now(),
            "mode": "DOWN",
            "stopped": stopped,
            "flags": {
                "WAZZUP_SEND_ENABLED": "false",
                "WAZZUP_AUTO_REPLY_ENABLED": "false",
                "AGENT7_LIVE_OUTREACH_ENABLED": "false",
            },
            "note": "persistent data kept; no queue/CRM deletion",
        },
    )
    print("=== local E2E DOWN ===")
    print(f"stopped: {', '.join(stopped) if stopped else '(none)'}")
    print("WAZZUP_SEND_ENABLED=false")
    print("WAZZUP_AUTO_REPLY_ENABLED=false")
    print("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF=false")
    print("AGENT7_LIVE_OUTREACH_ENABLED=false")
    print("CONTACT_ROLE_AUTO_ASSIGN_ENABLED=false")
    print("CONTACT_ROLE_AMO_SYNC_ENABLED=false")
    print("Phone social: OFF")
    print("Meta: OFF")
    print(f"runtime state kept under: {RUNTIME_DIR}")
    print(f"pids file: {PIDS_PATH}")
    print(f"tunnel meta: {TUNNEL_PATH}")
    print("queues/CRM/Notion: untouched")
    print()
    print(f"Re-check: python3 {REPO_ROOT / 'scripts' / 'local_e2e_status.py'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
