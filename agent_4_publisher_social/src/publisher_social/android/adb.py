from __future__ import annotations

import os
import hashlib
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


from ..channels.base import carousel_folder_name, marketplace_folder_name


def _media_set_digest(paths: list[str]) -> str:
    """Stable identity of the exact ordered media set downloaded from R2."""
    digest = hashlib.sha256()
    for value in paths:
        path = Path(value)
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def carousel_device_dir(base_media_dir: str | PurePosixPath, object_id: str) -> str:
    """One folder per object under publisher_social — not the shared flat gallery."""
    base = PurePosixPath(str(base_media_dir).rstrip("/"))
    return str(base / carousel_folder_name(object_id))


def marketplace_device_dir(marketplace_media_dir: str | PurePosixPath, object_id: str) -> str:
    """One folder per object for brand_open_home_url — separate from FB Groups carousel."""
    base = PurePosixPath(str(marketplace_media_dir).rstrip("/"))
    return str(base / marketplace_folder_name(object_id))


def verify_remote_paths(device: "AdbDevice", paths: list[str]) -> list[str]:
    """Return remote paths that are missing on the device after adb push."""
    missing: list[str] = []
    for remote in paths:
        if not remote:
            continue
        try:
            proc = device.run("shell", f"ls {shlex.quote(remote)}", check=False)
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0 or "No such file" in out:
                missing.append(remote)
        except Exception:
            missing.append(remote)
    return missing


@dataclass
class AdbDevice:
    serial: str | None
    adb_bin: str = "adb"
    media_dir: str = "/sdcard/Download/publisher_social"

    def _base(self) -> list[str]:
        cmd = [self.adb_bin]
        if self.serial:
            cmd.extend(["-s", self.serial])
        return cmd

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = self._base() + list(args)
        return subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=True,
        )

    def shell(self, *args: str, check: bool = True) -> str:
        proc = self.run("shell", *args, check=check)
        return (proc.stdout or "").strip()

    def shell_script(self, script: str, check: bool = True) -> str:
        proc = self.run("shell", script, check=check)
        return (proc.stdout or "").strip()

    def devices(self) -> list[str]:
        proc = subprocess.run(
            [self.adb_bin, "devices"],
            check=True,
            capture_output=True,
            text=True,
        )
        lines = (proc.stdout or "").splitlines()[1:]
        out: list[str] = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                out.append(parts[0])
        return out

    def ensure_media_dir(self) -> None:
        self.shell_script(f"mkdir -p {shlex.quote(self.media_dir)}")

    def push(
        self,
        local: Path,
        remote_name: str | None = None,
        *,
        media_dir: str | None = None,
    ) -> str:
        dest_dir = (media_dir or self.media_dir).rstrip("/")
        self.shell_script(f"mkdir -p {shlex.quote(dest_dir)}")
        name = remote_name or local.name
        remote = f"{dest_dir}/{name}"
        self.run("push", str(local), remote)
        # Триггер медиасканера, чтобы Instagram/TikTok/FB увидели файл
        self.shell_script(
            "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
            f"-d file:{shlex.quote(remote)}",
            check=False,
        )
        return remote

    def open_app(self, package: str) -> None:
        self.shell(
            "monkey",
            "-p",
            package,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
            check=False,
        )
        time.sleep(2)

    def wake(self) -> None:
        self.shell("input", "keyevent", "KEYCODE_WAKEUP", check=False)


def check_adb(android_cfg: dict[str, Any]) -> dict[str, Any]:
    device = AdbDevice(
        serial=android_cfg.get("serial") or os.environ.get("ANDROID_SERIAL"),
        adb_bin=android_cfg.get("adb_bin", "adb"),
        media_dir=android_cfg.get("media_dir", "/sdcard/Download/publisher_social"),
    )
    try:
        devices = device.devices()
    except FileNotFoundError:
        return {"ok": False, "error": "adb not found in PATH", "devices": []}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": e.stderr or str(e), "devices": []}

    serial = device.serial
    if serial and serial not in devices:
        return {
            "ok": False,
            "error": f"device {serial} not connected",
            "devices": devices,
        }
    if not serial and len(devices) == 1:
        serial = devices[0]
        device.serial = serial
    if not devices:
        return {
            "ok": False,
            "error": "no devices; enable USB/Wi‑Fi debugging and connect",
            "devices": [],
        }
    return {
        "ok": True,
        "serial": serial or devices[0],
        "devices": devices,
        "media_dir": device.media_dir,
        "packages": android_cfg.get("packages", {}),
    }


def reset_uiautomator(android_cfg: dict[str, Any] | None = None) -> None:
    """Сброс зависших UiAutomator / uiautomator2 — иначе «already registered»."""
    acfg = android_cfg or {}
    device = AdbDevice(
        serial=acfg.get("serial") or os.environ.get("ANDROID_SERIAL"),
        adb_bin=acfg.get("adb_bin", "adb"),
    )
    # Не давать экрану погаснуть во время длинного UI-прогона (~10 мин)
    device.shell("settings", "put", "system", "screen_off_timeout", "600000", check=False)
    device.shell("svc", "power", "stayon", "true", check=False)
    for pkg in (
        "com.github.uiautomator",
        "com.github.uiautomator.test",
        "io.appium.uiautomator2.server",
        "io.appium.uiautomator2.server.test",
        "com.wetest.uia2.server",
        "com.wetest.uia2.server.test",
    ):
        device.shell("am", "force-stop", pkg, check=False)
    time.sleep(1.5)


def push_files(
    device: AdbDevice,
    *,
    local_video: str | None,
    local_images: list[str],
    object_id: str,
    local_marketplace_images: list[str] | None = None,
    marketplace_media_dir: str | None = None,
) -> tuple[str | None, list[str], list[str]]:
    prefix = object_id.replace("/", "_")
    remote_video: str | None = None
    if local_video:
        video_dir = carousel_device_dir(device.media_dir, object_id)
        path = Path(local_video)
        remote_video = device.push(path, path.name, media_dir=video_dir)

    remote_images: list[str] = []
    carousel_media_dir = carousel_device_dir(device.media_dir, object_id)
    for local in local_images:
        path = Path(local)
        remote_images.append(
            device.push(
                path,
                path.name,
                media_dir=carousel_media_dir,
            )
        )

    if remote_images:
        missing = verify_remote_paths(device, remote_images)
        if missing:
            raise RuntimeError(
                "carousel media missing on device after push: "
                f"{missing[0]} (album={carousel_folder_name(object_id)})"
            )

    remote_mp: list[str] = []
    mp_dir = marketplace_device_dir(
        marketplace_media_dir or "/sdcard/Download/brand_open_home",
        object_id,
    )
    for local in local_marketplace_images or []:
        path = Path(local)
        remote_mp.append(device.push(path, path.name, media_dir=mp_dir))

    if remote_mp:
        missing = verify_remote_paths(device, remote_mp)
        if missing:
            raise RuntimeError(
                "marketplace media missing on device after push: "
                f"{missing[0]} (album={marketplace_folder_name(object_id)})"
            )

    return remote_video, remote_images, remote_mp
