"""Import-contract tests: legacy, compatibility, and canonical paths share objects."""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.mark.parametrize(
    "legacy, compat, canonical, symbol",
    [
        ("agent8.auto", "agent8.envoy.auto", "agent7_envoy.auto", "auto_outreach"),
        ("agent8.auto", "agent8.envoy.auto", "agent7_envoy.auto", "busy_message_for_client"),
        ("agent8.outreach", "agent8.envoy.outreach", "agent7_envoy.outreach", "build_outreach_plan"),
        ("agent8.outreach", "agent8.envoy.outreach", "agent7_envoy.outreach", "OwnerBusyInfo"),
        ("agent8.outreach", "agent8.envoy.outreach", "agent7_envoy.outreach", "precheck_alternatives"),
        ("agent8.outreach", "agent8.envoy.outreach", "agent7_envoy.outreach", "register_owner_whatsapp"),
        ("agent8.calendar_check", "agent8.envoy.calendar_check", "agent7_envoy.calendar_check", "check_calendar_dates"),
        ("agent8.calendar_check", "agent8.envoy.calendar_check", "agent7_envoy.calendar_check", "notion_update_from_precheck"),
        ("agent8.calendar_check", "agent8.envoy.calendar_check", "agent7_envoy.calendar_check", "format_busy_ranges"),
        ("agent8.owner_result", "agent8.envoy.owner_result", "agent7_envoy.owner_result", "OwnerVerdict"),
        ("agent8.owner_result", "agent8.envoy.owner_result", "agent7_envoy.owner_result", "build_client_message"),
        ("agent8.owner_result", "agent8.envoy.owner_result", "agent7_envoy.owner_result", "parse_owner_reply"),
        ("agent8.booking_doc", "agent8.notary.booking_doc", "agent8_notary.booking_doc", "generate_booking_doc"),
        ("agent8.booking_doc", "agent8.notary.booking_doc", "agent8_notary.booking_doc", "build_booking_data"),
        ("agent8.booking_doc", "agent8.notary.booking_doc", "agent8_notary.booking_doc", "RESERVATION_VALID_DAYS"),
        ("agent8.notary_service", "agent8.notary.service", "agent8_notary.service", "process_confirmed_booking"),
        ("agent8.notary_service", "agent8.notary.service", "agent8_notary.service", "NotaryBookingResult"),
    ],
)
def test_three_tier_import_same_object(legacy, compat, canonical, symbol):
    legacy_mod = importlib.import_module(legacy)
    compat_mod = importlib.import_module(compat)
    canonical_mod = importlib.import_module(canonical)
    obj = getattr(canonical_mod, symbol)
    assert getattr(legacy_mod, symbol) is obj
    assert getattr(compat_mod, symbol) is obj


def test_imports_do_not_touch_network(monkeypatch):
    """Импорт compatibility-путей не должен выполнять HTTP-запросы."""
    def fail_request(*args, **kwargs):
        raise AssertionError("network call during import")

    monkeypatch.setattr("requests.get", fail_request)
    monkeypatch.setattr("requests.post", fail_request)

    for name in (
        "agent7_envoy.auto",
        "agent7_envoy.outreach",
        "agent7_envoy.calendar_check",
        "agent7_envoy.owner_result",
        "agent8_notary.booking_doc",
        "agent8_notary.service",
        "agent8.auto",
        "agent8.outreach",
        "agent8.calendar_check",
        "agent8.owner_result",
        "agent8.booking_doc",
        "agent8.notary_service",
        "agent8.envoy.auto",
        "agent8.envoy.outreach",
        "agent8.envoy.calendar_check",
        "agent8.envoy.owner_result",
        "agent8.notary.booking_doc",
        "agent8.notary.service",
    ):
        importlib.import_module(name)


def test_notary_booking_doc_paths():
    from agent8_notary import booking_doc

    qualifier_root = Path(__file__).resolve().parents[1]
    assert booking_doc._QUALIFIER_ROOT == qualifier_root
    assert booking_doc._SCRIPT == qualifier_root / "scripts" / "generate_booking_request.js"
    assert booking_doc._SCRIPT.is_file()
    assert booking_doc._OUT_DIR == qualifier_root / "data" / "contracts"
    expected_config = booking_doc._QUALIFIER_ROOT.parent / "config" / "project.json"
    assert expected_config.is_file()
    assert expected_config == qualifier_root.parent / "config" / "project.json"
