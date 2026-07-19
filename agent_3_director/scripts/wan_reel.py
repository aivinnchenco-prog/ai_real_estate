#!/usr/bin/env python3
"""Wan 2.7 (fal.ai) i2v: фото → 9:16 клипы с прямым наплывом → рил.

Используется Agent 3 (renderWan.mjs). Требует FAL_KEY и ffmpeg в PATH.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

QUEUE_URL = "https://queue.fal.run/fal-ai/wan/v2.7/image-to-video"

PROMPT = (
    "Camera dolly moves straight forward along the central axis, directly ahead "
    "into the depth of the room, perfectly centered. Constant slow speed. "
    "Strictly no lateral movement, no sideways drift, no panning, no tilting, "
    "no rotation, no zoom bursts. No object animation, no moving furniture, "
    "no people. Static interior scene, only smooth forward camera motion. "
    "Photorealistic, bright natural lighting."
)


def _ffmpeg() -> str:
    path = os.environ.get("FFMPEG", "").strip() or shutil.which("ffmpeg") or ""
    if not path:
        raise RuntimeError("ffmpeg not found (install ffmpeg or set FFMPEG)")
    return path


def _ffprobe() -> str:
    path = os.environ.get("FFPROBE", "").strip() or shutil.which("ffprobe") or ""
    if not path:
        raise RuntimeError("ffprobe not found (install ffmpeg or set FFPROBE)")
    return path


def probe_duration(src: Path) -> float:
    proc = subprocess.run(
        [_ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
        capture_output=True, text=True, check=True,
    )
    return float(proc.stdout.strip())


def _load_key() -> str:
    return os.environ.get("FAL_KEY", "").strip()


def _request(url: str, key: str, payload: dict | None = None, method: str = "POST") -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} {method} {url.split('?')[0]}: {body}") from None


def crop_vertical(src: Path, dest: Path, ffmpeg: str) -> None:
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
         "-vf", "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920",
         "-q:v", "2", str(dest)],
        check=True,
    )


def generate(photo: Path, dest: Path, key: str, duration: int, resolution: str,
             prompt: str) -> bool:
    b64 = base64.b64encode(photo.read_bytes()).decode()
    payload = {
        "image_url": f"data:image/jpeg;base64,{b64}",
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
    }
    t0 = time.perf_counter()
    sub = _request(QUEUE_URL, key, payload)
    req_id = sub["request_id"]
    status_url = sub.get("status_url") or f"{QUEUE_URL}/requests/{req_id}/status"
    result_url = sub.get("response_url") or f"{QUEUE_URL}/requests/{req_id}"

    while True:
        time.sleep(4)
        st = _request(status_url, key, method="GET")
        if st.get("status") == "COMPLETED":
            break
        if st.get("status") in ("FAILED", "CANCELLED"):
            print(f"  FAIL {photo.name}: {st}")
            return False
        if time.perf_counter() - t0 > 600:
            print(f"  TIMEOUT {photo.name}")
            return False

    result = _request(result_url, key, method="GET")
    video_url = (result.get("video") or {}).get("url")
    if not video_url:
        print(f"  Нет video.url: {list(result)}")
        return False
    urllib.request.urlretrieve(video_url, dest)
    print(f"  {photo.name} -> {dest.name} за {time.perf_counter() - t0:.0f}с")
    return True


def trim(src: Path, dest: Path, seconds: float, ffmpeg: str) -> None:
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,fps=30"
    )
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
         "-t", f"{seconds:.2f}", "-vf", vf,
         "-c:v", "libx264", "-preset", "fast", "-crf", "19",
         "-pix_fmt", "yuv420p", "-an", str(dest)],
        check=True,
    )


def concat(clips: list[Path], dest: Path, ffmpeg: str) -> None:
    lst = dest.parent / "concat.txt"
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c:v", "libx264", "-preset", "fast", "-crf", "19",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)],
        check=True,
    )
    lst.unlink()


def apply_end_fade(src: Path, dest: Path, ffmpeg: str, fade_sec: float) -> None:
    """Лёгкое затемнение в конце ролика (fade to black)."""
    if fade_sec <= 0:
        shutil.copy2(src, dest)
        return
    duration = probe_duration(src)
    fade_sec = min(fade_sec, max(0.2, duration * 0.35))
    fade_start = max(0.0, duration - fade_sec)
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
         "-vf", f"fade=t=out:st={fade_start:.3f}:d={fade_sec:.3f}",
         "-c:v", "libx264", "-preset", "fast", "-crf", "19",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(dest)],
        check=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=9)
    ap.add_argument("--segment", type=float, default=1.6)
    ap.add_argument("--duration", type=int, default=2)
    ap.add_argument("--resolution", default="720p", choices=["720p", "1080p"])
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--end-fade", type=float, default=0.6,
                    help="длительность затемнения в конце ролика, сек (0 = выкл)")
    args = ap.parse_args()

    key = _load_key()
    if not key:
        print("Нет FAL_KEY")
        return 1

    ffmpeg = _ffmpeg()
    photos = sorted(
        p for p in Path(args.photos).iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )[: args.limit]
    outdir = Path(args.out)
    (outdir / "cropped").mkdir(parents=True, exist_ok=True)
    (outdir / "raw").mkdir(parents=True, exist_ok=True)
    (outdir / "trimmed").mkdir(parents=True, exist_ok=True)

    rate = 0.15 if args.resolution == "1080p" else 0.10
    print(
        f"Wan 2.7: {len(photos)} фото × {args.duration}с × ${rate}/с ≈ "
        f"${rate * args.duration * len(photos):.2f}"
    )

    trimmed: list[Path] = []
    for i, photo in enumerate(photos):
        cropped = outdir / "cropped" / f"{i + 1:03d}.jpg"
        raw = outdir / "raw" / f"clip_{i + 1:03d}.mp4"
        cut = outdir / "trimmed" / f"clip_{i + 1:03d}.mp4"
        if not raw.exists():
            crop_vertical(photo, cropped, ffmpeg)
            print(f"[{i + 1}/{len(photos)}] {photo.name}...")
            ok = False
            for attempt in range(2):
                try:
                    ok = generate(cropped, raw, key, args.duration, args.resolution, args.prompt)
                    break
                except RuntimeError as exc:
                    print(f"  retry {attempt + 1}: {exc}")
                    time.sleep(5)
            if not ok:
                continue
        else:
            print(f"[{i + 1}/{len(photos)}] {photo.name}: уже есть, пропуск")
        trim(raw, cut, args.segment, ffmpeg)
        trimmed.append(cut)

    if len(trimmed) < 2:
        print("Мало клипов для рила")
        return 1
    reel_raw = outdir / "reel_wan_raw.mp4"
    reel = outdir / "reel_wan.mp4"
    concat(trimmed, reel_raw, ffmpeg)
    if args.end_fade > 0:
        apply_end_fade(reel_raw, reel, ffmpeg, args.end_fade)
        reel_raw.unlink(missing_ok=True)
        print(f"Конец ролика: fade-out {args.end_fade:.1f}с")
    else:
        reel_raw.rename(reel)
    print(f"Рил готов: {reel} (~{args.segment * len(trimmed):.1f}с, {len(trimmed)} кадров)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
