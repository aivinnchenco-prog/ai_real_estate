"""Sanitized calendar fetch diagnostics and failure artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import resolve_runtime_dir
from .calendar_matcher import summarize_network_name

_SECRET_RE = re.compile(
    r"(password|authorization|cookie|set-cookie|proxy|credential|token|storage_state)",
    re.I,
)


def default_diagnostics_root() -> Path:
    return resolve_runtime_dir() / "diagnostics" / "airbnb_calendar"


@dataclass
class NetworkEvent:
    phase: str  # request | response | failed
    name: str
    url_hint: str
    status: int | None = None
    matched_calendar: bool = False


@dataclass
class CalendarFetchTrace:
    object_id: str = ""
    worker_id: str = ""
    requested_url: str = ""
    final_url: str = ""
    page_title: str = ""
    listing_id: str | None = None
    page_kind: str = ""
    check_result: str = ""
    message: str = ""
    attempt: int = 1
    domcontentloaded_ms: int | None = None
    networkidle: str = "skipped"
    listener_attached_before_navigation: bool = True
    calendar_responses_captured: int = 0
    calendar_ui_present: bool = False
    listing_markers_present: bool = False
    reload_retry_used: bool = False
    network_events: list[NetworkEvent] = field(default_factory=list)

    def to_report_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["network_events"] = data["network_events"][:40]
        return data


def _sanitize_text(text: str, limit: int = 2000) -> str:
    sample = (text or "")[:limit]
    for pat in (r"password=\S+", r"Bearer \S+", r"Basic \S+"):
        sample = re.sub(pat, "[redacted]", sample, flags=re.I)
    return sample


def save_failure_artifacts(
    trace: CalendarFetchTrace,
    *,
    screenshot_bytes: bytes | None = None,
    body_text: str = "",
    html_snippet: str = "",
) -> Path:
    root = default_diagnostics_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_oid = re.sub(r"[^\w.-]+", "_", trace.object_id or "unknown")
    out = root / f"{stamp}_{safe_oid}_{trace.worker_id or 'worker'}"
    out.mkdir(parents=True, exist_ok=True)

    report = trace.to_report_dict()
    (out / "trace.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if screenshot_bytes:
        (out / "screenshot.png").write_bytes(screenshot_bytes)
    if body_text:
        (out / "body.txt").write_text(_sanitize_text(body_text, 4000), encoding="utf-8")
    if html_snippet:
        (out / "page.html").write_text(_sanitize_text(html_snippet, 8000), encoding="utf-8")
    return out


def record_response_event(
    trace: CalendarFetchTrace,
    *,
    url: str,
    method: str,
    status: int,
    matched_calendar: bool,
) -> None:
    if len(trace.network_events) >= 60:
        return
    if not any(
        k in url.lower()
        for k in ("graphql", "availability", "calendar", "pdp", "stays", "/rooms/")
    ):
        return
    trace.network_events.append(
        NetworkEvent(
            phase="response",
            name=summarize_network_name(url, method),
            url_hint=url.split("?")[0][-120:],
            status=status,
            matched_calendar=matched_calendar,
        )
    )
