"""Background retry: добрать координаты Airbnb, если LOCATION_DEFAULT был пуст."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from CustomLogger import logger

_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_STOP = threading.Event()

MAX_ATTEMPTS = 8
RETRY_DELAYS = (120, 300, 600, 1200, 1800, 3600, 7200, 14400)


def _queue_path() -> Path:
    import config

    raw = str(
        getattr(config, "LOCATION_QUEUE_PATH", "")
        or getattr(config, "PRICE_QUEUE_PATH", "data/pricing_queue.json")
    )
    p = Path(raw)
    if p.name == "pricing_queue.json":
        return p.with_name("location_queue.json")
    if p.suffix == ".json":
        return p
    return p / "location_queue.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LocationQueue:
    def __init__(self, path: Path | None = None):
        self.path = path or _queue_path()
        self._data: dict[str, Any] = {"jobs": []}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"location queue read failed: {exc}")
            return
        self._data.setdefault("jobs", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def enqueue(
        self,
        *,
        object_id: str,
        listing_url: str,
        session_id: str = "",
        notion_page_id: str = "",
    ) -> None:
        with _LOCK:
            self.load()
            for job in self._data["jobs"]:
                if job.get("object_id") == object_id and job.get("status") in (
                    "pending",
                    "retry",
                    "running",
                ):
                    if listing_url:
                        job["listing_url"] = listing_url
                    if session_id:
                        job["session_id"] = session_id
                    if notion_page_id:
                        job["notion_page_id"] = notion_page_id
                    self.save()
                    return
            self._data["jobs"].append(
                {
                    "object_id": object_id,
                    "listing_url": listing_url,
                    "session_id": session_id,
                    "notion_page_id": notion_page_id,
                    "status": "pending",
                    "attempt": 0,
                    "next_retry_at": 0,
                    "created_at": _utc_now(),
                    "updated_at": _utc_now(),
                }
            )
            self.save()
            logger.info(f"location queue: enqueued {object_id}")

    def claim_next(self) -> dict[str, Any] | None:
        now = time.time()
        with _LOCK:
            self.load()
            for job in self._data["jobs"]:
                if job.get("status") not in ("pending", "retry"):
                    continue
                if float(job.get("next_retry_at") or 0) > now:
                    continue
                job["status"] = "running"
                job["updated_at"] = _utc_now()
                self.save()
                return dict(job)
        return None

    def mark_done(self, object_id: str) -> None:
        with _LOCK:
            self.load()
            for job in self._data["jobs"]:
                if job.get("object_id") == object_id:
                    job["status"] = "done"
                    job["updated_at"] = _utc_now()
            self.save()

    def mark_retry(self, object_id: str, *, reason: str = "") -> None:
        with _LOCK:
            self.load()
            for job in self._data["jobs"]:
                if job.get("object_id") != object_id:
                    continue
                attempt = int(job.get("attempt") or 0) + 1
                job["attempt"] = attempt
                job["last_error"] = reason
                job["updated_at"] = _utc_now()
                if attempt >= MAX_ATTEMPTS:
                    job["status"] = "failed"
                else:
                    delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
                    job["status"] = "retry"
                    job["next_retry_at"] = time.time() + delay
            self.save()


_QUEUE: LocationQueue | None = None


def get_location_queue() -> LocationQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = LocationQueue()
    return _QUEUE


def ensure_location_worker() -> None:
    global _WORKER
    with _LOCK:
        if _WORKER and _WORKER.is_alive():
            return
        _STOP.clear()
        _WORKER = threading.Thread(
            target=_worker_loop, daemon=True, name="location-bg-worker"
        )
        _WORKER.start()
        logger.info("location worker started")


def bootstrap_location_retry() -> bool:
    ensure_location_worker()
    q = get_location_queue()
    pending = sum(
        1 for j in q._data.get("jobs", []) if j.get("status") in ("pending", "retry")
    )
    logger.info(f"location worker bootstrap: pending/retry={pending}")
    return True


def enqueue_location_retry(
    *,
    object_id: str,
    listing_url: str,
    session_id: str = "",
    notion_page_id: str = "",
) -> None:
    if not object_id or not listing_url:
        return
    get_location_queue().enqueue(
        object_id=object_id,
        listing_url=listing_url,
        session_id=session_id,
        notion_page_id=notion_page_id,
    )
    ensure_location_worker()


def _apply_location_to_session_and_notion(
    job: dict[str, Any], location: dict[str, Any]
) -> None:
    """Обновляет parsed.json сессии и Notion Google Maps / Район / Адрес."""
    import sys

    from agent2_handoff import find_agent2_root

    lat = float(location["latitude"])
    lng = float(location["longitude"])
    subtitle = (location.get("subtitle") or "").strip()

    agent2 = find_agent2_root()
    if not agent2:
        raise RuntimeError("agent2 root not found")

    session_id = job.get("session_id") or ""
    if session_id:
        parsed_path = agent2 / "data" / "sessions" / session_id / "parsed.json"
        if parsed_path.exists():
            meta = json.loads(parsed_path.read_text(encoding="utf-8"))
            meta["location"] = {
                "latitude": lat,
                "longitude": lng,
                "subtitle": subtitle,
            }
            parsed_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    scripts = agent2 / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    if str(agent2) not in sys.path:
        sys.path.insert(0, str(agent2))

    from maps_resolver import coords_point_url, district_from_coords, resolve_google_maps
    from real_estate_handler import NotionCRM
    import os

    district = district_from_coords(lat, lng) or "Phuket"
    desc = ""
    title = job.get("object_id") or ""
    if session_id:
        desc_path = agent2 / "data" / "sessions" / session_id / "description.txt"
        if desc_path.exists():
            desc = desc_path.read_text(encoding="utf-8")
            title = desc.splitlines()[0] if desc.strip() else title

    maps = resolve_google_maps(
        desc,
        district,
        title,
        coords=(lat, lng),
    )
    page_id = job.get("notion_page_id") or ""
    if not page_id and session_id:
        sj = agent2 / "data" / "sessions" / session_id / "session.json"
        if sj.exists():
            page_id = (json.loads(sj.read_text(encoding="utf-8")).get("notion_page_id") or "")

    if not page_id:
        raise RuntimeError(f"no notion_page_id for {job.get('object_id')}")

    crm = NotionCRM(
        os.environ["NOTION_API_KEY"],
        os.environ.get("NOTION_DB_ID") or os.environ["NOTION_DATABASE_ID"],
    )
    props = {
        "Google Maps": NotionCRM.build_url(maps.url or coords_point_url(lat, lng)),
        "Район": NotionCRM.build_text(maps.district or district),
        "Адрес": NotionCRM.build_text(subtitle or maps.address or ""),
    }
    crm.update_page(page_id, props)
    logger.info(
        f"location retry OK {job.get('object_id')}: "
        f"{lat:.5f},{lng:.5f} → {maps.url} ({maps.district})"
    )


def _fetch_location(listing_url: str) -> dict[str, Any]:
    from airbnb_parser import AirbnbParser

    parser = AirbnbParser(headless=True)
    try:
        _msg, _media, data = parser.process_url(listing_url)
        loc = data.get("Локация") or {}
        if loc.get("latitude") is None or loc.get("longitude") is None:
            return {}
        return loc
    finally:
        try:
            parser.close()
        except Exception:
            pass


def _worker_loop() -> None:
    q = get_location_queue()
    while not _STOP.is_set():
        job = q.claim_next()
        if not job:
            time.sleep(15)
            continue
        oid = job.get("object_id") or ""
        url = job.get("listing_url") or ""
        try:
            loc = _fetch_location(url)
            if not loc:
                q.mark_retry(oid, reason="location still empty")
                continue
            _apply_location_to_session_and_notion(job, loc)
            q.mark_done(oid)
        except Exception as exc:
            logger.warning(f"location retry failed {oid}: {exc}")
            q.mark_retry(oid, reason=str(exc)[:200])
        time.sleep(5)
