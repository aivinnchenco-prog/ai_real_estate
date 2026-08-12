#!/usr/bin/env python3
"""Start NORMAL Google Chrome with CDP (no Playwright launch flags).

Required for WhatsApp Lists UI. Playwright-launched Chrome serves a different UI.

Local:
  python3 scripts/whatsapp_ui_open_native_chrome.py

VPS (no display):
  WHATSAPP_UI_USE_XVFB=true python3 scripts/whatsapp_ui_open_native_chrome.py --daemon

Workers attach with:
  WHATSAPP_UI_CDP_URL=http://127.0.0.1:9222
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "whatsapp_ui_sync" / "src"))

for env_path in (ROOT / ".env", ROOT / "agent_6_qualifier" / ".env"):
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from whatsapp_ui_sync.chrome_bin import default_native_profile_dir, find_chrome_binary

PORT = int(os.getenv("WHATSAPP_UI_CDP_PORT") or "9222")
URL = os.getenv("WHATSAPP_UI_URL") or "https://web.whatsapp.com/"
PID_FILE = Path(
    os.getenv("WHATSAPP_UI_CHROME_PID_FILE")
    or (Path.home() / ".openhome" / "whatsapp_ui_sync" / "chrome.pid")
)


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _use_xvfb() -> bool:
    raw = (os.getenv("WHATSAPP_UI_USE_XVFB") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    # Auto on Linux without DISPLAY
    return sys.platform.startswith("linux") and not (os.getenv("DISPLAY") or "").strip()


def build_chrome_cmd(chrome: Path, profile: Path, port: int) -> list[str]:
    cmd = [
        str(chrome),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={str(profile)}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        "--window-size=1400,900",
        URL,
    ]
    # Linux server: avoid sandbox issues in containers
    if sys.platform.startswith("linux"):
        if (os.getenv("WHATSAPP_UI_CHROME_NO_SANDBOX") or "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            cmd.insert(1, "--no-sandbox")
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Exit after CDP is up (for systemd Type=simple wrapper use without --daemon hold)",
    )
    parser.add_argument(
        "--hold",
        action="store_true",
        default=True,
        help="Keep process alive while CDP listens (default)",
    )
    parser.add_argument("--no-hold", action="store_true", help="Exit after start")
    args = parser.parse_args()
    hold = not args.no_hold and not args.daemon

    chrome = find_chrome_binary()
    if chrome is None:
        print(
            "Google Chrome / Chromium not found. Install google-chrome-stable "
            "or set WHATSAPP_UI_CHROME_BIN=",
            file=sys.stderr,
        )
        return 1

    profile = default_native_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    if _port_open(PORT):
        print(f"CDP already listening on :{PORT}")
        print(f"WHATSAPP_UI_CDP_URL=http://127.0.0.1:{PORT}")
        if not hold:
            return 0
        print("Holding…")
        try:
            while _port_open(PORT):
                time.sleep(2)
        except KeyboardInterrupt:
            print("\nStopped waiting.")
        return 0

    chrome_cmd = build_chrome_cmd(chrome, profile, PORT)
    use_xvfb = _use_xvfb()
    if use_xvfb:
        if not shutil_which("xvfb-run"):
            print(
                "DISPLAY empty and xvfb-run not found. Install: apt install xvfb",
                file=sys.stderr,
            )
            return 1
        full_cmd = ["xvfb-run", "-a", "-s", "-screen 0 1400x900x24", *chrome_cmd]
    else:
        full_cmd = chrome_cmd

    print("=" * 60)
    print("Launching NORMAL Chrome (no Playwright flags)")
    print(f"Binary:  {chrome}")
    print(f"Profile: {profile}")
    print(f"CDP:     http://127.0.0.1:{PORT}")
    print(f"Xvfb:    {use_xvfb}")
    print("QR login is MANUAL. Wazzup linked device untouched.")
    print("=" * 60)

    proc = subprocess.Popen(
        full_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")

    for _ in range(60):
        if _port_open(PORT):
            break
        if proc.poll() is not None:
            print("Chrome exited early", file=sys.stderr)
            return 1
        time.sleep(0.25)
    if not _port_open(PORT):
        print("CDP port did not open", file=sys.stderr)
        return 1

    print(f"Chrome PID={proc.pid} CDP ready.")
    print(f"WHATSAPP_UI_CDP_URL=http://127.0.0.1:{PORT}")
    if not hold:
        return 0

    print("Holding… close Chrome or Ctrl+C.")
    try:
        while _port_open(PORT):
            time.sleep(2)
            if proc.poll() is not None:
                break
    except KeyboardInterrupt:
        print("\nStopped waiting (Chrome may still be open).")
    return 0


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


if __name__ == "__main__":
    raise SystemExit(main())
