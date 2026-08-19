"""Controlled Facebook ACTIVE/SOLD verification using fixed reference URLs."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import AvailabilityConfig, load_config
from .error_notify import maybe_notify_facebook_session_restored
from .models import PropertySource, SourceKind
from ..providers.facebook import FacebookAvailabilityProvider
from ..providers.facebook_checker import (
    REFERENCE_ACTIVE_ITEM_ID,
    REFERENCE_ACTIVE_URL,
    REFERENCE_SOLD_ITEM_ID,
    REFERENCE_SOLD_URL,
)


@dataclass
class FacebookVerifyCase:
    label: str
    url: str
    expected_item_id: str
    expected_status: str
    detected_status: str = ""
    outcome: str = ""
    status_reason: str = ""
    passed: bool = False


@dataclass
class FacebookVerifyReport:
    cases: list[FacebookVerifyCase] = field(default_factory=list)
    signal: str = "Rented badge → SOLD; no badge + listing loaded → ACTIVE; relay is_sold when present"
    verified: bool = False
    blocked: bool = True
    error: str = ""


def run_facebook_verify(
    config: AvailabilityConfig | None = None,
    *,
    confirm_live: bool = False,
) -> FacebookVerifyReport:
    if not confirm_live:
        raise SystemExit("REFUSED: facebook-verify requires --confirm-live")

    config = config or load_config()
    if not config.facebook_live_allowed:
        raise SystemExit(
            "STOP: AVAILABILITY_ENABLED=true, AVAILABILITY_DRY_RUN=false, "
            "AVAILABILITY_FACEBOOK_ENABLED=true required"
        )

    report = FacebookVerifyReport()
    provider = FacebookAvailabilityProvider()

    specs = (
        ("ACTIVE", REFERENCE_ACTIVE_URL, REFERENCE_ACTIVE_ITEM_ID, "ACTIVE"),
        ("SOLD", REFERENCE_SOLD_URL, REFERENCE_SOLD_ITEM_ID, "SOLD"),
    )
    for label, url, item_id, expected in specs:
        case = FacebookVerifyCase(
            label=label,
            url=url,
            expected_item_id=item_id,
            expected_status=expected,
        )
        prop = PropertySource(
            object_id=f"FB_VERIFY_{label}",
            name=f"Facebook verify {label}",
            source=SourceKind.FACEBOOK,
            source_url=url,
        )
        try:
            result = provider.check_listing_status(prop, config)
            case.outcome = result.outcome
            case.status_reason = result.status_reason
            case.detected_status = result.business_status or result.outcome
            case.passed = result.business_status == expected
        except Exception as exc:
            case.outcome = "BROWSER_ERROR"
            case.status_reason = str(exc)[:500]
            case.detected_status = "ERROR"
            case.passed = False
        report.cases.append(case)

    report.verified = all(c.passed for c in report.cases)
    report.blocked = not report.verified
    if report.verified:
        maybe_notify_facebook_session_restored()
    return report


def print_facebook_verify_report(report: FacebookVerifyReport) -> None:
    print("\n== FACEBOOK REFERENCE VERIFICATION ==")
    print(f"  signal: {report.signal}")
    for case in report.cases:
        status = "PASS" if case.passed else "FAIL"
        print(f"  [{status}] {case.label}")
        print(f"    url: {case.url}")
        print(f"    expected: {case.expected_status} (id={case.expected_item_id})")
        print(f"    detected: {case.detected_status} (outcome={case.outcome})")
        if case.status_reason:
            print(f"    reason: {case.status_reason}")
    print(f"\n  VERIFIED: {'YES' if report.verified else 'NO'}")
    if report.blocked:
        print("  GLOBAL ENABLE BLOCKED until verification passes")
