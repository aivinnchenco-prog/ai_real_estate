#!/usr/bin/env python3
"""Start local E2E infrastructure in SAFE mode (no live WA send / Agent7 / Meta / phone social).

Usage:
  python3 scripts/local_e2e_up.py
  python3 scripts/local_e2e_up.py --strategy wa_wa
  python3 scripts/local_e2e_up.py --no-tunnel   # webhook only, skip cloudflared/PATCH
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
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
    TUNNEL_PATH,
    WEBHOOK_HEALTH,
    WEBHOOK_HOST,
    WEBHOOK_PATH,
    WEBHOOK_PORT,
    LauncherConfig,
    RuntimeState,
    apply_safe_flags,
    ensure_runtime_dirs,
    http_ok,
    load_dotenv_file,
    montage_lock_held,
    parse_cloudflared_url,
    pid_alive,
    probe_url,
    property_entrypoints,
    refuse_phone_social_start,
    save_json,
    spawn_logged,
    stop_pid,
    utc_now,
    wait_public_webhook_ready,
)


def _start_webhook(state: RuntimeState) -> None:
    from local_e2e_lib import local_webhook_ready, port_listening

    existing = state.pids.get("wazzup_webhook")
    if existing and pid_alive(existing) and local_webhook_ready():
        state.services["wazzup_webhook"] = {
            "status": "reused",
            "pid": existing,
            "url": WEBHOOK_HEALTH,
        }
        return
    if existing and pid_alive(existing) and not local_webhook_ready():
        raise SystemExit(
            f"REFUSE: wazzup_webhook pid {existing} alive but health/test ping FAIL — "
            "run local_e2e_down.py first"
        )

    # Healthy listener already bound (started outside launcher) — reuse, no duplicate.
    if local_webhook_ready():
        state.services["wazzup_webhook"] = {
            "status": "reused_external",
            "pid": None,
            "url": WEBHOOK_HEALTH,
            "note": "port already serving webhook",
        }
        return

    if port_listening(WEBHOOK_HOST, WEBHOOK_PORT):
        raise SystemExit(
            f"REFUSE: {WEBHOOK_HOST}:{WEBHOOK_PORT} is in use but webhook health/test "
            "ping failed — stop the old process, then re-run local_e2e_up.py"
        )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(AGENT6_ROOT / "src")
    env["WAZZUP_WEBHOOK_ENABLED"] = "true"
    env["WAZZUP_SEND_ENABLED"] = "false"
    env["WAZZUP_AUTO_REPLY_ENABLED"] = "false"
    env["AGENT7_LIVE_OUTREACH_ENABLED"] = "false"
    pid = spawn_logged(
        "wazzup_webhook",
        [
            sys.executable,
            str(AGENT6_ROOT / "scripts" / "wazzup_webhook_serve.py"),
        ],
        cwd=AGENT6_ROOT,
        env=env,
    )
    state.pids["wazzup_webhook"] = pid
    ok = False
    for _ in range(40):
        if local_webhook_ready():
            ok = True
            break
        time.sleep(0.15)
    state.services["wazzup_webhook"] = {
        "status": "READY" if ok else "FAIL",
        "pid": pid,
        "url": WEBHOOK_HEALTH,
    }
    if not ok:
        raise SystemExit("FAIL: wazzup webhook /health not ready")


def _start_chain(state: RuntimeState) -> None:
    existing = state.pids.get("chain_watcher")
    if existing and pid_alive(existing):
        state.services["chain_watcher"] = {"status": "reused", "pid": existing}
        return
    if not CHAIN_RUNNER.exists():
        raise SystemExit(f"FAIL: chain_runner missing: {CHAIN_RUNNER}")
    env = os.environ.copy()
    # Load assistant-media envs if present (Notion keys).
    load_dotenv_file(ASSISTANT_MEDIA / ".env.real-estate")
    load_dotenv_file(ASSISTANT_MEDIA / ".env")
    load_dotenv_file(REPO_ROOT / ".env")
    pid = spawn_logged(
        "chain_watcher",
        [sys.executable, str(CHAIN_RUNNER), "--watch"],
        cwd=ASSISTANT_MEDIA,
        env=env,
    )
    time.sleep(0.5)
    alive = pid_alive(pid)
    state.pids["chain_watcher"] = pid
    state.services["chain_watcher"] = {
        "status": "RUNNING" if alive else "FAIL",
        "pid": pid,
        "cwd": str(ASSISTANT_MEDIA),
        "cmd": "python3 scripts/chain_runner.py --watch",
    }
    if not alive:
        raise SystemExit("FAIL: chain_watcher exited immediately")


def _start_tunnel(state: RuntimeState) -> str:
    # Avoid colliding with orphan quick tunnels from previous failed runs.
    subprocess.run(
        ["pkill", "-f", f"cloudflared tunnel --url http://{WEBHOOK_HOST}:{WEBHOOK_PORT}"],
        check=False,
        capture_output=True,
    )
    time.sleep(0.5)

    existing = state.pids.get("cloudflared")
    tunnel = state.tunnel or {}
    public = str(tunnel.get("public_base") or "").rstrip("/")
    if existing and pid_alive(existing) and public:
        if wait_public_webhook_ready(public, timeout_s=10.0):
            state.services["cloudflared"] = {
                "status": "reused",
                "pid": existing,
                "public_base": public,
            }
            return public
        stop_pid(existing, name="cloudflared")
        state.pids.pop("cloudflared", None)

    last_url = ""
    for attempt in range(1, 4):
        env = os.environ.copy()
        pid = spawn_logged(
            "cloudflared",
            [
                "cloudflared",
                "tunnel",
                "--url",
                f"http://{WEBHOOK_HOST}:{WEBHOOK_PORT}",
            ],
            cwd=REPO_ROOT,
            env=env,
            reset_log=True,
        )
        state.pids["cloudflared"] = pid
        url = None
        log_path = RUNTIME_DIR / "logs" / "cloudflared.log"
        for _ in range(80):
            if log_path.exists():
                text = log_path.read_text(encoding="utf-8", errors="replace")
                url = parse_cloudflared_url(text)
                if url:
                    break
            if not pid_alive(pid):
                break
            time.sleep(0.25)
        if not url:
            stop_pid(pid, name="cloudflared")
            state.pids.pop("cloudflared", None)
            continue
        last_url = url
        # Quick tunnels sometimes advertise a hostname that does not resolve yet;
        # retry a fresh tunnel instead of hanging forever.
        if wait_public_webhook_ready(url, timeout_s=35.0):
            state.tunnel = {
                "public_base": url,
                "webhook_uri": f"{url}{WEBHOOK_PATH}",
                "pid": pid,
                "updated_at": utc_now(),
                "attempt": attempt,
            }
            state.services["cloudflared"] = {
                "status": "READY",
                "pid": pid,
                "public_base": url,
                "attempt": attempt,
            }
            save_json(TUNNEL_PATH, state.tunnel)
            return url
        stop_pid(pid, name="cloudflared")
        state.pids.pop("cloudflared", None)
        time.sleep(1.0)

    state.services["cloudflared"] = {
        "status": "FAIL",
        "public_base": last_url or None,
        "reason": "public test ping not ready after retries",
    }
    raise SystemExit(
        "FAIL: cloudflared quick tunnel public webhook not reachable "
        f"(last_url={'set' if last_url else 'none'}); retry local_e2e_up.py"
    )


def _patch_wazzup(public_base: str, state: RuntimeState) -> None:
    load_dotenv_file(AGENT6_ENV, override=True)
    sys.path.insert(0, str(AGENT6_ROOT / "src"))
    from agent6_qualifier.messaging.wazzup_client import WazzupClient
    from agent6_qualifier.messaging.wazzup_config import load_wazzup_config

    webhook_uri = f"{public_base.rstrip('/')}{WEBHOOK_PATH}"
    cfg = load_wazzup_config()
    if not cfg.api_key_set:
        state.services["wazzup_webhook_patch"] = {
            "status": "FAIL",
            "reason": "WAZZUP_API_KEY missing",
        }
        raise SystemExit("FAIL: WAZZUP_API_KEY missing — cannot PATCH webhook URI")
    client = WazzupClient(cfg)
    client.set_webhooks_uri(webhook_uri)
    # GET verify
    payload = client.get_webhooks()
    uri = ""
    if isinstance(payload, dict):
        uri = str(
            payload.get("webhooksUri")
            or payload.get("webhookUri")
            or payload.get("uri")
            or ""
        )
    ok_get = webhook_uri.rstrip("/") in uri.rstrip("/") or uri.rstrip("/") == webhook_uri.rstrip("/")
    public_health = probe_url(f"{public_base.rstrip('/')}/health")
    state.services["wazzup_webhook_patch"] = {
        "status": "READY" if ok_get else "FAIL",
        "webhook_uri_set": True,
        "get_verify": "OK" if ok_get else "FAIL",
        "public_health": public_health,
        # never store API key
    }
    state.tunnel["webhook_uri"] = webhook_uri
    state.tunnel["patched_at"] = utc_now()
    save_json(TUNNEL_PATH, state.tunnel)
    if not ok_get:
        raise SystemExit("FAIL: Wazzup GET /v3/webhooks did not confirm new URI")


def _maybe_start_property_input(cfg: LauncherConfig, state: RuntimeState) -> None:
    if cfg.start_property_input == "none":
        state.services["agent1_input"] = {
            "status": "OFF",
            "note": "start manually via printed entrypoint",
        }
        return
    if cfg.start_property_input == "airbnb":
        if not AIRBNB_MAIN.exists():
            raise SystemExit(f"FAIL: missing {AIRBNB_MAIN}")
        existing = state.pids.get("agent1_airbnb")
        if existing and pid_alive(existing):
            state.services["agent1_input"] = {"status": "reused", "pid": existing}
            return
        pid = spawn_logged(
            "agent1_airbnb",
            [sys.executable, str(AIRBNB_MAIN)],
            cwd=AIRBNB_MAIN.parent,
        )
        state.pids["agent1_airbnb"] = pid
        state.services["agent1_input"] = {
            "status": "READY",
            "pid": pid,
            "kind": "airbnb",
        }
        return
    if cfg.start_property_input == "fb":
        if not FB_BOT.exists():
            raise SystemExit(f"FAIL: missing {FB_BOT}")
        existing = state.pids.get("agent1_fb")
        if existing and pid_alive(existing):
            state.services["agent1_input"] = {"status": "reused", "pid": existing}
            return
        pid = spawn_logged(
            "agent1_fb",
            [sys.executable, str(FB_BOT)],
            cwd=FB_BOT.parents[1],
        )
        state.pids["agent1_fb"] = pid
        state.services["agent1_input"] = {
            "status": "READY",
            "pid": pid,
            "kind": "fb_marketplace",
        }
        return
    raise SystemExit(f"Unknown --start-property-input={cfg.start_property_input}")


def _print_banner(cfg: LauncherConfig, state: RuntimeState) -> None:
    load_dotenv_file(AGENT6_ENV)
    confirmed = (
        os.getenv("WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF", "false")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    print("=== local E2E UP (SAFE mode) ===")
    print(f"runtime: {RUNTIME_DIR}")
    print(f"strategy: {cfg.strategy}")
    print("WAZZUP_SEND_ENABLED=false")
    print("WAZZUP_AUTO_REPLY_ENABLED=false")
    print("AGENT7_LIVE_OUTREACH_ENABLED=false")
    print("Phone social: OFF")
    print("Meta Ads: OFF")
    print("Agent5/9/10: OFF")
    if not confirmed:
        print()
        print("WA LIVE SEND: BLOCKED")
        print("REASON: EXTERNAL AUTORESPONSE NOT CONFIRMED OFF")
    print()
    print("PROPERTY INPUT ENTRYPOINTS:")
    for name, cmd in property_entrypoints().items():
        print(f"  {name}:")
        print(f"    {cmd}")
    print()
    if cfg.strategy == "wa_wa":
        print("Telegram Agent6/7 client runtime: not required for WA+WA")
    print()
    for key in ("wazzup_webhook", "chain_watcher", "cloudflared", "wazzup_webhook_patch"):
        svc = state.services.get(key) or {}
        print(f"{key}: {svc.get('status', 'OFF')}")
    if state.tunnel.get("public_base"):
        print(f"public_base: {state.tunnel['public_base']}")
        print(f"webhook_uri: {state.tunnel.get('webhook_uri')}")
    print()
    print("Next:")
    print(f"  python3 {REPO_ROOT / 'scripts' / 'local_e2e_status.py'}")
    print(f"  python3 {REPO_ROOT / 'scripts' / 'local_e2e_down.py'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local E2E infrastructure UP (safe)")
    parser.add_argument("--strategy", default="wa_wa", choices=["wa_wa", "tg_required"])
    parser.add_argument(
        "--start-property-input",
        default="none",
        choices=["none", "airbnb", "fb"],
        help="Optional: start Agent1 bot process (default: print entrypoint only)",
    )
    parser.add_argument(
        "--enable-phone-social",
        action="store_true",
        help="Forbidden when pending social jobs exist; default OFF",
    )
    parser.add_argument("--enable-meta", action="store_true", help="REFUSED")
    parser.add_argument("--no-tunnel", action="store_true")
    parser.add_argument("--no-chain", action="store_true")
    parser.add_argument("--no-webhook", action="store_true")
    parser.add_argument("--no-wazzup-patch", action="store_true")
    args = parser.parse_args()

    cfg = LauncherConfig(
        strategy=args.strategy,
        start_property_input=args.start_property_input,
        enable_phone_social=bool(args.enable_phone_social),
        enable_meta=bool(args.enable_meta),
        patch_wazzup_webhook=not args.no_wazzup_patch and not args.no_tunnel,
        start_tunnel=not args.no_tunnel,
        start_chain=not args.no_chain,
        start_webhook=not args.no_webhook,
        safe_mode=True,
    )

    if cfg.enable_meta:
        raise SystemExit("REFUSE START: Meta Ads must stay OFF for local E2E V1")
    if cfg.enable_phone_social:
        refuse_phone_social_start(enable_phone_social=True)
        raise SystemExit(
            "REFUSE START: phone social is out of V1 scope "
            "(agent_4_publisher_social must not start)"
        )

    ensure_runtime_dirs()
    load_dotenv_file(REPO_ROOT / ".env")
    load_dotenv_file(AGENT6_ENV, override=True)
    apply_safe_flags(env_path=AGENT6_ENV)

    held, holder = montage_lock_held()
    if held:
        print(f"NOTE: montage flock currently held ({holder}) — chain may skip montage")

    state = RuntimeState.load()
    state.services["phone_social"] = {"status": "OFF"}
    state.services["meta_ads"] = {"status": "OFF"}
    state.services["agent5"] = {"status": "OFF"}
    state.services["agent9"] = {"status": "OFF"}
    state.services["agent10"] = {"status": "OFF"}
    state.services["contact_role_outbox_replay"] = {"status": "OFF", "note": "no replay"}
    state.services["wa_session_auto_replay"] = {"status": "OFF"}

    try:
        if cfg.start_webhook:
            _start_webhook(state)
        if cfg.start_chain:
            _start_chain(state)
        tunnel_ok = True
        if cfg.start_tunnel:
            try:
                public = _start_tunnel(state)
                if cfg.patch_wazzup_webhook:
                    _patch_wazzup(public, state)
            except SystemExit as exc:
                tunnel_ok = False
                state.services.setdefault("cloudflared", {})["status"] = "FAIL"
                state.services.setdefault("wazzup_webhook_patch", {})["status"] = "FAIL"
                state.services["cloudflared"]["error"] = str(exc)
                print(f"WARN: {exc}")
                print(
                    "Continuing with local webhook + chain watcher. "
                    "Re-run local_e2e_up.py when trycloudflare DNS is healthy."
                )
        else:
            state.services["cloudflared"] = {"status": "OFF", "note": "--no-tunnel"}
            state.services["wazzup_webhook_patch"] = {"status": "OFF"}
            # Do not advertise a stale public URL from a previous run.
            state.tunnel = {
                "public_base": "",
                "webhook_uri": "",
                "updated_at": utc_now(),
                "note": "tunnel skipped",
            }
        _maybe_start_property_input(cfg, state)
    finally:
        state.services["updated_at"] = utc_now()
        state.save()
        save_json(
            STATUS_PATH,
            {
                "updated_at": utc_now(),
                "mode": "SAFE",
                "services": state.services,
                "tunnel": {
                    "public_base": state.tunnel.get("public_base"),
                    "webhook_uri": state.tunnel.get("webhook_uri"),
                },
                "pids": {k: v for k, v in state.pids.items()},
            },
        )

    _print_banner(cfg, state)
    if cfg.start_tunnel and not tunnel_ok:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
