"""Regression tests for calendar fetch/parser (no live Airbnb)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from availability_service.app.airbnb_workers.calendar_fetch import (
    _parse_calendar_payload,
    normalize_listing_url,
)
from availability_service.app.airbnb_workers.calendar_matcher import (
    is_calendar_api_payload,
    is_calendar_api_url,
)
from availability_service.app.airbnb_workers.calendar_trace import (
    CalendarFetchTrace,
    save_failure_artifacts,
)
from availability_service.app.airbnb_workers.models import CalendarCheckResult, CheckResult
from availability_service.app.airbnb_workers.page_state import (
    PageKind,
    classify_page_kind,
    page_kind_to_check_result,
)


def _sample_calendar_payload() -> dict:
    return {
        "data": {
            "merlin": {
                "pdpAvailabilityCalendar": {
                    "calendarMonths": [
                        {
                            "days": [
                                {"calendarDate": "2026-09-01", "available": True},
                                {"calendarDate": "2026-09-02", "available": False},
                            ]
                        }
                    ]
                }
            }
        }
    }


def test_listener_attached_before_navigation_source_order():
    src = Path(__file__).resolve().parents[1] / "app/airbnb_workers/calendar_fetch.py"
    text = src.read_text(encoding="utf-8")
    assert text.index('page.on("response"') < text.index("page.goto(")


def test_delayed_calendar_response_matcher():
    payload = _sample_calendar_payload()
    assert is_calendar_api_url("https://www.airbnb.com/api/v3/PdpAvailabilityCalendar/abc")
    assert is_calendar_api_payload(payload)
    days = _parse_calendar_payload([payload])
    assert days[date(2026, 9, 1)] is True
    assert days[date(2026, 9, 2)] is False


def test_graphql_operation_name_matcher():
    url = "https://www.airbnb.com/api/v3/StaysPdpAvailabilityCalendar/hash"
    assert is_calendar_api_url(url)
    alt = {
        "data": {
            "pdpAvailabilityCalendar": {
                "calendarMonths": [{"days": [{"calendarDate": "2026-09-01", "available": True}]}]
            }
        }
    }
    assert is_calendar_api_payload(alt)


def test_generic_homepage_classified_not_parse_error():
    kind = classify_page_kind(
        requested_url="https://www.airbnb.com/rooms/123",
        final_url="https://www.airbnb.com/",
        title="Airbnb | Vacation rentals, cabins, beach houses, & more",
        body_text="Explore homes",
    )
    assert kind == PageKind.GENERIC_HOMEPAGE
    assert page_kind_to_check_result(kind) == CheckResult.GENERIC_HOMEPAGE


def test_removed_listing_not_parse_error():
    kind = classify_page_kind(
        requested_url="https://www.airbnb.com/rooms/999",
        final_url="https://www.airbnb.com/rooms/999",
        title="This listing is no longer available",
        body_text="This listing is no longer available",
    )
    assert kind in {PageKind.NOT_FOUND, PageKind.LISTING_REMOVED}
    assert page_kind_to_check_result(kind) != CheckResult.PARSE_ERROR


def test_listing_page_without_calendar_is_parse_error():
    kind = classify_page_kind(
        requested_url="https://www.airbnb.com/rooms/555",
        final_url="https://www.airbnb.com/rooms/555",
        title="Villa in Phuket",
        body_text="Book it now. Check-in. Where you'll sleep.",
    )
    assert kind == PageKind.LISTING_PAGE
    assert page_kind_to_check_result(kind) == CheckResult.PARSE_ERROR


def test_failure_never_implies_success():
    for status in (
        CheckResult.PARSE_ERROR,
        CheckResult.GENERIC_HOMEPAGE,
        CheckResult.LISTING_UNAVAILABLE,
        CheckResult.CAPTCHA,
    ):
        result = CalendarCheckResult(status=status)
        assert result.should_not_update_availability is True


def test_diagnostic_artifacts_contain_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "availability_service.app.airbnb_workers.calendar_trace.default_diagnostics_root",
        lambda: tmp_path,
    )
    trace = CalendarFetchTrace(
        object_id="A_test",
        worker_id="worker_01",
        requested_url="https://www.airbnb.com/rooms/1",
    )
    body = "password=secret123 Authorization: Bearer abc cookie=xyz"
    out = save_failure_artifacts(trace, body_text=body)
    saved = (out / "body.txt").read_text(encoding="utf-8")
    assert "secret123" not in saved
    assert "Bearer abc" not in saved


def test_normalize_listing_url_strips_tracking():
    raw = "https://www.airbnb.ru/rooms/123?check_in=2026-09-01&currency=THB"
    assert normalize_listing_url(raw) == "https://www.airbnb.com/rooms/123"
