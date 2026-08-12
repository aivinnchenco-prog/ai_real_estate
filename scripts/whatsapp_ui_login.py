#!/usr/bin/env python3
"""Manual WhatsApp Web login via NORMAL Chrome + CDP.

Do NOT use Playwright-launched Chrome — Lists UI breaks.
On VPS: start Chrome (possibly under Xvfb), then scan QR.
Optional QR screenshot path for headless servers.
"""

from __future__ import annotations

import os
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

from whatsapp_ui_sync.browser_session import BrowserSessionStatus, WhatsAppBrowserSession
from whatsapp_ui_sync.config import WhatsAppUiSyncConfig


def _ensure_cdp(port: int = 9222) -> str:
    url = (os.getenv("WHATSAPP_UI_CDP_URL") or "").strip()
    if url:
        return url
    # Start native chrome if needed
    opener = ROOT / "scripts" / "whatsapp_ui_open_native_chrome.py"
    subprocess.run(
        [sys.executable, str(opener), "--no-hold"],
        check=False,
    )
    cdp = f"http://127.0.0.1:{port}"
    os.environ["WHATSAPP_UI_CDP_URL"] = cdp
    return cdp


def _maybe_save_qr(session: WhatsAppBrowserSession, out: Path) -> None:
    try:
        page = session.page
        if page is None:
            return
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out), full_page=False)
        print(f"Screenshot saved (scan QR if visible): {out}")
    except Exception as exc:
        print(f"QR screenshot skipped: {exc}")


def _wait_until_ready(session: WhatsAppBrowserSession, timeout_sec: int = 900) -> BrowserSessionStatus:
    deadline = time.time() + timeout_sec
    qr_path = Path(
        os.getenv("WHATSAPP_UI_QR_SCREENSHOT")
        or (Path.home() / ".openhome" / "whatsapp_ui_sync" / "qr.png")
    )
    print("\nОтсканируйте QR телефоном (Linked devices).")
    print("Wazzup НЕ трогаем.")
    _maybe_save_qr(session, qr_path)
    last_shot = time.time()
    while time.time() < deadline:
        probe = session.probe_auth()
        print(f"  status={probe.status.value} — {probe.message}")
        if probe.status == BrowserSessionStatus.READY:
            return probe.status
        if probe.status == BrowserSessionStatus.LINKED_DEVICE_CONFLICT:
            return probe.status
        if probe.status == BrowserSessionStatus.AUTH_REQUIRED and time.time() - last_shot > 20:
            _maybe_save_qr(session, qr_path)
            last_shot = time.time()
        if sys.stdin.isatty():
            time.sleep(3)
            try:
                import select

                if select.select([sys.stdin], [], [], 0.0)[0]:
                    sys.stdin.readline()
                    probe = session.probe_auth()
                    if probe.status == BrowserSessionStatus.READY:
                        return probe.status
            except Exception:
                pass
        else:
            time.sleep(3)
    return BrowserSessionStatus.FAILED


def main() -> int:
    port = int(os.getenv("WHATSAPP_UI_CDP_PORT") or "9222")
    cdp = _ensure_cdp(port)
    os.environ["WHATSAPP_UI_CDP_URL"] = cdp
    cfg = WhatsAppUiSyncConfig.from_env()
    print("=" * 60)
    print("WhatsApp UI login (native Chrome + CDP)")
    print(f"CDP:     {cfg.cdp_url}")
    print(f"Profile: {cfg.profile_dir} (Chrome user-data-dir)")
    print("=" * 60)
    session = WhatsAppBrowserSession(cfg)
    try:
        session.start()
        session.open_whatsapp()
        status = _wait_until_ready(session)
        if status == BrowserSessionStatus.READY:
            print("\nOK: WhatsApp Web READY. Keep Chrome/CDP running for sync worker.")
            return 0
        if status == BrowserSessionStatus.LINKED_DEVICE_CONFLICT:
            print("\nSTOP: linked device conflict. Wazzup untouched.")
            return 2
        print("\nFAILED: login not confirmed.")
        return 1
    finally:
        # Disconnect Playwright only — do not kill Chrome
        session.stop()


if __name__ == "__main__":
    raise SystemExit(main())
