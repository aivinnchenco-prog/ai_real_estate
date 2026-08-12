"""Shared helpers for local E2E launcher (no business-logic changes)."""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO_ROOT / "runtime" / "local_e2e"
PIDS_PATH = RUNTIME_DIR / "pids.json"
SERVICES_PATH = RUNTIME_DIR / "services.json"
TUNNEL_PATH = RUNTIME_DIR / "tunnel.json"
STATUS_PATH = RUNTIME_DIR / "status.json"
FLAGS_BACKUP_PATH = RUNTIME_DIR / "flags_backup.json"
LOG_DIR = RUNTIME_DIR / "logs"

ASSISTANT_MEDIA = (
    REPO_ROOT / "agent_2_registrar" / "_import" / "assistant-media"
)
CHAIN_RUNNER = ASSISTANT_MEDIA / "scripts" / "chain_runner.py"
CHAIN_WATCHER_SH = ASSISTANT_MEDIA / "scripts" / "chain_watcher.sh"
MONTAGE_LOCK = ASSISTANT_MEDIA / "data" / "montage.lock"
SOCIAL_JOBS_DIR = REPO_ROOT / "agent_4_publisher_social" / "data" / "jobs"
AGENT6_ROOT = REPO_ROOT / "agent_6_qualifier"
AGENT6_ENV = AGENT6_ROOT / ".env"
REPO_ENV = REPO_ROOT / ".env"

AIRBNB_MAIN = REPO_ROOT / "agent_1_parser" / "airbnb_parser" / "main.py"
FB_BOT = REPO_ROOT / "agent_1_parser" / "fb_parser" / "agent1b" / "tg_bot.py"

WEBHOOK_HOST = "127.0.0.1"
WEBHOOK_PORT = 8765
WEBHOOK_HEALTH = f"http://{WEBHOOK_HOST}:{WEBHOOK_PORT}/health"
WEBHOOK_PATH = "/webhooks/wazzup"

TRYCLOUDFLARE_RE = re.compile(
    r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE
)

SAFE_FLAG_DEFAULTS = {
    "WAZZUP_SEND_ENABLED": "false",
    "WAZZUP_AUTO_REPLY_ENABLED": "false",
    "WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF": "false",
    "AGENT7_LIVE_OUTREACH_ENABLED": "false",
    "AGENT7_FACEBOOK_MESSENGER_ENABLED": "false",
    "AGENT7_AIRBNB_MESSAGES_ENABLED": "false",
    "WAZZUP_STAGE_MODE": "true",
    "WAZZUP_LIVE_ALLOWLIST_ENABLED": "true",
    "WAZZUP_WEBHOOK_ENABLED": "true",
    "CONTACT_ROLE_AUTO_ASSIGN_ENABLED": "false",
    "CONTACT_ROLE_AMO_SYNC_ENABLED": "false",
    "WHATSAPP_UI_SYNC_ENABLED": "false",
    "META_ADS_ENABLED": "false",
    "META_WRITE_ENABLED": "false",
    "META_ACTIVE_ENABLED": "false",
    "PUBLISHER_SOCIAL_ENABLED": "false",
    "FB_GROUPS_PHONE_PUBLISHER_ENABLED": "false",
    "FB_MARKETPLACE_PHONE_PUBLISHER_ENABLED": "false",
}

SECRET_KEY_FRAGMENTS = (
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "BEARER",
    "AUTHORIZATION",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_runtime_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default if default is not None else {}


def save_json(path: Path, payload: Any) -> None:
    ensure_runtime_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def redact_for_log(text: str) -> str:
    out = text
    for frag in SECRET_KEY_FRAGMENTS:
        out = re.sub(
            rf"({frag}\s*[=:]\s*)([^\s,;]+)",
            r"\1***",
            out,
            flags=re.IGNORECASE,
        )
    out = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", out, flags=re.I)
    return out


def load_dotenv_file(path: Path, *, override: bool = False) -> dict[str, str]:
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def upsert_env_keys(path: Path, updates: dict[str, str]) -> None:
    """Update keys in .env without printing values."""
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")


def read_env_key(path: Path, key: str, default: str = "") -> str:
    if not path.exists():
        return default
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        k, _, v = text.partition("=")
        if k.strip() == key:
            return v.strip().strip("'").strip('"')
    return default


def apply_safe_flags(*, env_path: Path | None = None) -> dict[str, str]:
    """Force safe live flags OFF. Returns previous values for backup."""
    path = env_path or AGENT6_ENV
    previous: dict[str, str] = {}
    for key in SAFE_FLAG_DEFAULTS:
        previous[key] = read_env_key(path, key, os.getenv(key, ""))
    upsert_env_keys(path, dict(SAFE_FLAG_DEFAULTS))
    for key, value in SAFE_FLAG_DEFAULTS.items():
        os.environ[key] = value
    # Mirror critical kill switches into repo .env if present.
    if REPO_ENV.exists() or path != REPO_ENV:
        mirror = {
            k: SAFE_FLAG_DEFAULTS[k]
            for k in (
                "META_ADS_ENABLED",
                "META_WRITE_ENABLED",
                "META_ACTIVE_ENABLED",
                "PUBLISHER_SOCIAL_ENABLED",
                "FB_GROUPS_PHONE_PUBLISHER_ENABLED",
                "FB_MARKETPLACE_PHONE_PUBLISHER_ENABLED",
            )
            if k in SAFE_FLAG_DEFAULTS
        }
        if REPO_ENV.exists() or mirror:
            upsert_env_keys(REPO_ENV if REPO_ENV.exists() else path, mirror)
    save_json(FLAGS_BACKUP_PATH, {"saved_at": utc_now(), "previous": previous})
    return previous


def force_live_flags_off(*, env_path: Path | None = None) -> None:
    path = env_path or AGENT6_ENV
    updates = {
        "WAZZUP_SEND_ENABLED": "false",
        "WAZZUP_AUTO_REPLY_ENABLED": "false",
        "WAZZUP_EXTERNAL_AUTORESPONSE_CONFIRMED_OFF": "false",
        "AGENT7_LIVE_OUTREACH_ENABLED": "false",
        "AGENT7_FACEBOOK_MESSENGER_ENABLED": "false",
        "AGENT7_AIRBNB_MESSAGES_ENABLED": "false",
        "CONTACT_ROLE_AUTO_ASSIGN_ENABLED": "false",
        "CONTACT_ROLE_AMO_SYNC_ENABLED": "false",
        "WHATSAPP_UI_SYNC_ENABLED": "false",
        "META_ADS_ENABLED": "false",
        "META_WRITE_ENABLED": "false",
        "META_ACTIVE_ENABLED": "false",
        "PUBLISHER_SOCIAL_ENABLED": "false",
        "FB_GROUPS_PHONE_PUBLISHER_ENABLED": "false",
        "FB_MARKETPLACE_PHONE_PUBLISHER_ENABLED": "false",
    }
    upsert_env_keys(path, updates)
    for k, v in updates.items():
        os.environ[k] = v
    if REPO_ENV.exists():
        upsert_env_keys(
            REPO_ENV,
            {
                "META_ADS_ENABLED": "false",
                "META_WRITE_ENABLED": "false",
                "META_ACTIVE_ENABLED": "false",
                "PUBLISHER_SOCIAL_ENABLED": "false",
                "FB_GROUPS_PHONE_PUBLISHER_ENABLED": "false",
                "FB_MARKETPLACE_PHONE_PUBLISHER_ENABLED": "false",
            },
        )


def pending_social_jobs(jobs_dir: Path | None = None) -> list[Path]:
    root = jobs_dir or SOCIAL_JOBS_DIR
    if not root.is_dir():
        return []
    out: list[Path] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out.append(path)
            continue
        # Treat unfinished / queued phone-social payloads as pending.
        status = str(data.get("status") or data.get("state") or "").lower()
        channels_pending = data.get("channels_pending") or data.get("pending") or []
        if status in {"done", "completed", "verified", "cancelled", "canceled"}:
            continue
        if isinstance(channels_pending, list) and not channels_pending and status in {
            "done",
            "completed",
        }:
            continue
        # Job files in this tree are publish payloads; any intact job is pending risk.
        out.append(path)
    return out


def refuse_phone_social_start(*, enable_phone_social: bool, jobs_dir: Path | None = None) -> None:
    if not enable_phone_social:
        return
    pending = pending_social_jobs(jobs_dir)
    if pending:
        names = ", ".join(p.name for p in pending[:8])
        extra = f" (+{len(pending) - 8} more)" if len(pending) > 8 else ""
        raise SystemExit(
            "REFUSE START: phone social requested but pending old jobs exist in "
            f"agent_4_publisher_social/data/jobs/: {names}{extra}"
        )


def montage_lock_held(lock_path: Path | None = None) -> tuple[bool, str]:
    """True only if real flock is held. Stale text in file is NOT a held lock."""
    path = lock_path or MONTAGE_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                holder = os.read(fd, 256).decode("utf-8", errors="replace").strip()
            except OSError:
                holder = ""
            return True, holder or "unknown"
        # Acquired → not held by another process; release immediately.
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False, ""
    finally:
        os.close(fd)


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def http_status(url: str, *, method: str = "GET", body: bytes | None = None, timeout: float = 3.0) -> int | None:
    try:
        req = Request(url, data=body, method=method.upper())
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status)
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            return code
        return None


def http_ok(url: str, *, method: str = "GET", timeout: float = 3.0) -> bool:
    code = http_status(url, method=method, timeout=timeout)
    return code is not None and 200 <= code < 300


def local_webhook_ready() -> bool:
    """True if local Wazzup webhook responds (health or legacy test ping)."""
    if http_ok(WEBHOOK_HEALTH):
        return True
    # Legacy server without /health: POST test ping is safe (no message send).
    code = http_status(
        f"http://{WEBHOOK_HOST}:{WEBHOOK_PORT}{WEBHOOK_PATH}",
        method="POST",
        body=b'{"test":true}',
    )
    return code is not None and 200 <= code < 300


def port_listening(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def probe_url(url: str, *, method: str = "GET", timeout: float = 3.0) -> str:
    if not url:
        return "missing"
    try:
        req = Request(url, method=method.upper())
        req.add_header("Accept", "application/json")
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            code = int(resp.status)
            return "READY" if 200 <= code < 300 else f"FAIL (HTTP {code})"
    except URLError as exc:
        return f"FAIL ({exc.reason or exc})"
    except Exception as exc:  # noqa: BLE001
        return f"FAIL ({type(exc).__name__})"


def spawn_logged(
    name: str,
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    reset_log: bool = False,
) -> int:
    ensure_runtime_dirs()
    log_path = LOG_DIR / f"{name}.log"
    mode = "w" if reset_log else "a"
    log_f = open(log_path, mode, encoding="utf-8")  # noqa: SIM115
    log_f.write(f"\n--- start {utc_now()} cmd={' '.join(argv)} ---\n")
    log_f.flush()
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=env or os.environ.copy(),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return int(proc.pid)


def parse_cloudflared_url(text: str) -> str | None:
    matches = TRYCLOUDFLARE_RE.findall(text or "")
    return matches[-1] if matches else None


def wait_public_webhook_ready(public_base: str, *, timeout_s: float = 45.0) -> bool:
    """Wait until trycloudflare forwards POST test ping to local webhook."""
    uri = f"{public_base.rstrip('/')}{WEBHOOK_PATH}"
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        code = http_status(uri, method="POST", body=b'{"test":true}', timeout=8.0)
        last = code
        if code is not None and 200 <= code < 300:
            return True
        # Fallback: curl often succeeds when urllib hits transient tunnel warmup issues.
        try:
            proc = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    "--max-time",
                    "8",
                    "-X",
                    "POST",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    '{"test":true}',
                    uri,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            curl_code = (proc.stdout or "").strip()
            last = curl_code
            if curl_code.isdigit() and 200 <= int(curl_code) < 300:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.75)
    return False


def stop_pid(pid: int | None, *, name: str = "") -> None:
    if not pid_alive(pid):
        return
    assert pid is not None
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    for _ in range(20):
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


@dataclass
class LauncherConfig:
    strategy: str = "wa_wa"  # wa_wa | tg_required
    start_property_input: str = "none"  # none | airbnb | fb
    enable_phone_social: bool = False
    enable_meta: bool = False
    patch_wazzup_webhook: bool = True
    start_tunnel: bool = True
    start_chain: bool = True
    start_webhook: bool = True
    safe_mode: bool = True


@dataclass
class RuntimeState:
    pids: dict[str, int] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)
    tunnel: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "RuntimeState":
        return cls(
            pids={k: int(v) for k, v in load_json(PIDS_PATH, {}).items() if str(v).isdigit()},
            services=load_json(SERVICES_PATH, {}),
            tunnel=load_json(TUNNEL_PATH, {}),
        )

    def save(self) -> None:
        save_json(PIDS_PATH, self.pids)
        save_json(SERVICES_PATH, self.services)
        save_json(TUNNEL_PATH, self.tunnel)


def property_entrypoints() -> dict[str, str]:
    return {
        "airbnb": (
            f"cd {REPO_ROOT / 'agent_1_parser' / 'airbnb_parser'} && python3 main.py"
        ),
        "fb_marketplace": (
            f"cd {REPO_ROOT / 'agent_1_parser' / 'fb_parser'} && "
            "python3 agent1b/tg_bot.py"
        ),
    }
