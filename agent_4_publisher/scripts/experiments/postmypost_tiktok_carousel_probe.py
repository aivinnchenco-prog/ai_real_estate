#!/usr/bin/env python3
"""
DEV-ONLY: PostMyPost TikTok photo carousel API probe.

Not imported by Agent 4 production pipeline.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request
from zoneinfo import ZoneInfo

BASE_URL = "https://api.postmypost.io/v4.1"
PROJECT_ID = 355063
TIKTOK_ACCOUNT_ID = 2214123
TIMEZONE = "Asia/Bangkok"
PUBLICATION_TYPE_POST = 1
PUBLICATION_STATUS_WAITING = 5
DEFAULT_SLIDES_DIR = (
    Path(__file__).resolve().parents[3]
    / "agent_4_publisher_social/data/media/A_20260806_001"
)
REPORT_DIR = Path(__file__).resolve().parents[2] / "data/experiments"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def token() -> str:
    tok = os.environ.get("POSTMYPOST_API_TOKEN") or os.environ.get("POSTMYPOST_TOKEN")
    if not tok:
        raise SystemExit("POSTMYPOST_API_TOKEN missing in agent_4_publisher/.env")
    return tok


def api_request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    url = f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/json",
        "User-Agent": "tiktok-carousel-probe/1.0",
    }
    data: bytes | None = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = request.Request(url, data=data, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {method} {path}: {err_body}") from exc


def list_slide_jpegs(slides_dir: Path, limit: int | None = None) -> list[Path]:
    slides = sorted(slides_dir.glob("slide_*.jpg"))
    if not slides:
        raise SystemExit(f"No slide_*.jpg in {slides_dir}")
    for slide in slides:
        if slide.suffix.lower() not in {".jpg", ".jpeg"}:
            raise SystemExit(f"Not JPEG: {slide}")
    if limit is not None:
        slides = slides[:limit]
    return slides


def upload_jpeg(path: Path, *, min_interval: float = 6.5) -> int:
    size = path.stat().st_size
    init = api_request(
        "POST",
        "/upload/init",
        {"project_id": PROJECT_ID, "name": path.name, "size": size},
    )
    upload_id = init.get("id")
    action = init.get("action")
    fields = init.get("fields") or []
    if upload_id is None or not action:
        raise RuntimeError(f"upload/init failed for {path.name}: {init}")

    boundary = f"----probe-{uuid.uuid4().hex}"
    body = bytearray()
    for field in fields:
        key = field.get("key")
        value = field.get("value")
        if key is None or value is None:
            continue
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.extend(f"{value}\r\n".encode())
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode()
    )
    body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
    body.extend(path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())

    s3_req = request.Request(
        action,
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with request.urlopen(s3_req, timeout=180) as resp:
            if resp.status not in (200, 201, 204, 302):
                raise RuntimeError(f"S3 upload HTTP {resp.status} for {path.name}")
    except error.HTTPError as exc:
        raise RuntimeError(
            f"S3 upload failed for {path.name}: HTTP {exc.code} {exc.read()[:300]!r}"
        ) from exc

    time.sleep(min_interval)
    api_request("POST", f"/upload/complete?id={upload_id}")
    # poll status
    for _ in range(45):
        time.sleep(2)
        status = api_request("GET", f"/upload/status?id={upload_id}")
        state = int(status.get("status") or 0)
        if state == 1 and status.get("file_id") is not None:
            time.sleep(min_interval)
            return int(status["file_id"])
        if state == 2:
            raise RuntimeError(f"upload failed for {path.name}: {status}")
    raise RuntimeError(f"upload timeout for {path.name} (id={upload_id})")


def format_post_at(dt_utc: datetime, tz_name: str) -> str:
    local = dt_utc.astimezone(ZoneInfo(tz_name))
    return local.isoformat(timespec="seconds")


def build_payload(
    *,
    file_ids: list[int],
    image_count: int,
    post_at: str,
) -> dict[str, Any]:
    return {
        "project_id": PROJECT_ID,
        "post_at": post_at,
        "account_ids": [TIKTOK_ACCOUNT_ID],
        "publication_status": PUBLICATION_STATUS_WAITING,
        "details": [
            {
                "publication_type": PUBLICATION_TYPE_POST,
                "content": f"API TEST — TikTok photo carousel — {image_count} images",
                "title": f"API TEST carousel {image_count}",
                "file_ids": file_ids,
                "tiktok_comment": True,
                "tiktok_duet": True,
                "tiktok_stitch": True,
            }
        ],
    }


def sanitize_publication(pub: dict[str, Any]) -> dict[str, Any]:
    details = []
    for detail in pub.get("details") or []:
        files = detail.get("files") or []
        exts: dict[str, int] = {}
        for f in files:
            orig = str(f.get("original") or "")
            ext = orig.rsplit(".", 1)[-1].lower() if "." in orig else "unknown"
            exts[ext] = exts.get(ext, 0) + 1
        details.append(
            {
                "publication_type": detail.get("publication_type"),
                "file_ids": detail.get("file_ids"),
                "files_count": len(files),
                "file_ext_counts": exts,
                "content_preview": (detail.get("content") or "")[:80],
            }
        )
    posts = []
    for post in pub.get("posts") or []:
        posts.append(
            {
                "account_id": post.get("account_id"),
                "post_status": post.get("post_status"),
                "url": post.get("url"),
                "external_id": post.get("external_id"),
            }
        )
    return {
        "id": pub.get("id"),
        "publication_status": pub.get("publication_status"),
        "post_at": pub.get("post_at"),
        "account_ids": pub.get("account_ids"),
        "details": details,
        "posts": posts,
        "errors": pub.get("errors"),
    }


def delete_publication(publication_id: int | str) -> bool:
    try:
        api_request("DELETE", f"/publications/{publication_id}")
        return True
    except RuntimeError as exc:
        print(f"DELETE {publication_id} failed: {exc}", file=sys.stderr)
        return False


def poll_publication(publication_id: int | str, *, attempts: int = 30, sleep_s: int = 20) -> dict[str, Any]:
    last: dict[str, Any] = {}
    for i in range(attempts):
        last = api_request("GET", f"/publications/{publication_id}")
        status = int(last.get("publication_status") or 0)
        posts = last.get("posts") or []
        has_url = any((p.get("url") or "").startswith("http") for p in posts)
        if status == 1 and has_url:
            return last
        if status not in (5, 1):
            return last
        if i + 1 < attempts:
            time.sleep(sleep_s)
    return last


def classify_url(url: str | None) -> str:
    if not url:
        return "none"
    u = url.lower()
    if "/video/" in u:
        return "video"
    if "/photo/" in u or "/post/" in u:
        return "photo"
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser(description="PostMyPost TikTok carousel API probe")
    parser.add_argument("--slides-dir", type=Path, default=DEFAULT_SLIDES_DIR)
    parser.add_argument(
        "--counts",
        default="2",
        help="Comma-separated image counts to test, e.g. 2,5,10",
    )
    parser.add_argument(
        "--schedule-minutes",
        type=int,
        default=25,
        help="Minutes from now for post_at",
    )
    parser.add_argument("--dry-run", action="store_true", help="Upload + print payload only")
    parser.add_argument("--execute", action="store_true", help="Perform POST /publications")
    parser.add_argument("--max-create-attempts", type=int, default=8)
    parser.add_argument("--poll-live", action="store_true", help="Poll one publication until live URL")
    parser.add_argument("--delete-id", type=int, help="Delete a scheduled test publication by id")
    args = parser.parse_args()

    env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_path)

    if args.delete_id:
        ok = delete_publication(args.delete_id)
        print(json.dumps({"deleted": ok, "id": args.delete_id}, indent=2))
        return 0 if ok else 1

    counts = [int(x.strip()) for x in args.counts.split(",") if x.strip()]
    slides_all = list_slide_jpegs(args.slides_dir)
    max_needed = max(counts)
    if len(slides_all) < max_needed:
        raise SystemExit(f"Need {max_needed} slides, found {len(slides_all)} in {args.slides_dir}")

    post_at = format_post_at(
        datetime.now(timezone.utc) + timedelta(minutes=args.schedule_minutes),
        TIMEZONE,
    )

    # Upload pool once (up to max_needed)
    upload_map: dict[str, int] = {}
    print(f"Uploading up to {max_needed} JPEG slides from {args.slides_dir} ...")
    for slide in slides_all[:max_needed]:
        print(f"  upload {slide.name} ...", flush=True)
        upload_map[slide.name] = upload_jpeg(slide)
    print(json.dumps({"uploads": upload_map}, indent=2))

    report_path = REPORT_DIR / f"tiktok_carousel_probe_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    report: dict[str, Any] = {
        "probe_id": report_path.stem,
        "slides_dir": str(args.slides_dir),
        "uploads": upload_map,
        "post_at": post_at,
        "attempts": [],
    }

    create_attempts = 0
    results_table: list[dict[str, Any]] = []

    for count in counts:
        if create_attempts >= args.max_create_attempts:
            print(f"Stopping: max create attempts ({args.max_create_attempts}) reached")
            break
        slide_names = [p.name for p in slides_all[:count]]
        file_ids = [upload_map[name] for name in slide_names]
        payload = build_payload(file_ids=file_ids, image_count=count, post_at=post_at)
        sanitized_payload = json.loads(json.dumps(payload))
        row = {
            "images": count,
            "post_accepted": False,
            "scheduled": None,
            "published": False,
            "publication_id": None,
            "result": "not_run",
            "error": None,
            "url_pattern": None,
            "media_kind": None,
        }
        print("\n=== SANITIZED PAYLOAD (no token) ===")
        print(json.dumps(sanitized_payload, indent=2, ensure_ascii=False))

        if not args.execute:
            row["result"] = "dry_run_only"
            results_table.append(row)
            report["attempts"].append({"count": count, "payload": sanitized_payload, "row": row})
            continue

        create_attempts += 1
        try:
            created = api_request("POST", "/publications", payload)
            pub_id = created.get("id")
            row["post_accepted"] = pub_id is not None
            row["publication_id"] = pub_id
            row["scheduled"] = post_at
            row["result"] = "created"
            print(f"\nPOST accepted publication id={pub_id}")
            got = api_request("GET", f"/publications/{pub_id}")
            sanitized = sanitize_publication(got)
            print(json.dumps(sanitized, indent=2, ensure_ascii=False))
            report["attempts"].append(
                {"count": count, "payload": sanitized_payload, "create_response_id": pub_id, "get": sanitized}
            )

            if count == counts[0] and args.poll_live and pub_id:
                print("\nPolling for live publication ...")
                live = poll_publication(pub_id, attempts=45, sleep_s=20)
                sanitized_live = sanitize_publication(live)
                print(json.dumps(sanitized_live, indent=2, ensure_ascii=False))
                posts = sanitized_live.get("posts") or []
                url = posts[0].get("url") if posts else None
                row["published"] = int(sanitized_live.get("publication_status") or 0) == 1 and bool(url)
                row["url_pattern"] = classify_url(url)
                row["media_kind"] = row["url_pattern"]
                row["result"] = "live" if row["published"] else f"status_{sanitized_live.get('publication_status')}"
                report["live_poll"] = sanitized_live
            elif pub_id:
                status = int(sanitize_publication(got).get("publication_status") or 0)
                row["scheduled"] = status == 5
                row["result"] = "scheduled" if status == 5 else f"status_{status}"

            # Only first successful create kept for live; delete extra scheduled if still waiting
            if count != counts[0] and pub_id and row.get("scheduled") is True:
                deleted = delete_publication(pub_id)
                row["result"] += ";deleted_before_publish" if deleted else ";delete_failed"

        except RuntimeError as exc:
            row["post_accepted"] = False
            row["error"] = str(exc)[:500]
            row["result"] = "rejected"
            print(f"POST rejected for {count} images: {exc}", file=sys.stderr)
            report["attempts"].append({"count": count, "payload": sanitized_payload, "error": row["error"]})
            if "422" in row["error"] or "too many" in row["error"].lower():
                break

        results_table.append(row)
        time.sleep(6.5)

    report["results_table"] = results_table
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport saved: {report_path}")

    print("\n=== RESULTS TABLE ===")
    print("| Images | POST accepted | Scheduled | Published | Result |")
    print("| -----: | ------------- | --------- | --------- | ------ |")
    for row in results_table:
        print(
            f"| {row['images']:>6} | "
            f"{'YES' if row['post_accepted'] else 'NO':>13} | "
            f"{'YES' if row.get('scheduled') else 'NO':>9} | "
            f"{'YES' if row.get('published') else 'NO':>9} | "
            f"{row.get('result', '')} |"
        )

    if results_table and results_table[0]["post_accepted"]:
        print("\nTikTok accepted multi-JPEG publication: YES")
    elif args.execute:
        print("\nTikTok accepted multi-JPEG publication: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
