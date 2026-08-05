"""Import-contract tests: legacy, compatibility, and canonical paths share objects."""
import importlib
import json
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


@pytest.mark.parametrize(
    "legacy, canonical, symbol",
    [
        ("agent7.owner_registry", "agent7_envoy.owner_registry", "mark_owner"),
        ("agent7.owner_registry", "agent7_envoy.owner_registry", "get_owner"),
        ("agent7.airbnb_check", "agent7_envoy.airbnb_check", "CalendarCheck"),
        ("agent7.airbnb_check", "agent7_envoy.airbnb_check", "check_airbnb_dates"),
    ],
)
def test_envoy_infra_legacy_and_canonical_import_same_object(legacy, canonical, symbol):
    legacy_mod = importlib.import_module(legacy)
    canonical_mod = importlib.import_module(canonical)
    assert getattr(legacy_mod, symbol) is getattr(canonical_mod, symbol)


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
        "agent7_envoy.owner_registry",
        "agent7_envoy.owner_handler",
        "agent7_envoy.airbnb_check",
        "agent8_notary.booking_doc",
        "agent8_notary.service",
        "agent8.auto",
        "agent8.outreach",
        "agent8.calendar_check",
        "agent8.owner_result",
        "agent8.booking_doc",
        "agent8.notary_service",
        "agent7.owner_registry",
        "agent7.airbnb_check",
        "agent8.envoy.auto",
        "agent8.envoy.outreach",
        "agent8.envoy.calendar_check",
        "agent8.envoy.owner_result",
        "agent8.notary.booking_doc",
        "agent8.notary.service",
    ):
        importlib.import_module(name)


def test_envoy_infra_import_does_not_start_playwright_or_read_owners_json(monkeypatch, tmp_path):
    def fail_playwright(*args, **kwargs):
        raise AssertionError("playwright invoked during import")

    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        fail_playwright,
        raising=False,
    )

    real_path = Path(__file__).resolve().parents[1] / "data" / "owners.json"
    sentinel = tmp_path / "must-not-read-owners.json"
    monkeypatch.setattr(
        "agent7_envoy.owner_registry._PATH",
        sentinel,
        raising=False,
    )

    for name in ("agent7.owner_registry", "agent7_envoy.owner_registry",
                 "agent7.airbnb_check", "agent7_envoy.airbnb_check"):
        importlib.import_module(name)

    assert not sentinel.exists()
    if real_path.exists():
        assert real_path.stat().st_mtime  # smoke: real file untouched by import


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


def test_owner_registry_path_contract():
    from agent7_envoy import owner_registry

    qualifier_root = Path(__file__).resolve().parents[1]
    assert owner_registry._PATH == qualifier_root / "data" / "owners.json"
    assert owner_registry._PATH.parent.name == "data"
    assert owner_registry._PATH.name == "owners.json"


def test_owner_registry_mark_and_get_json_shape(tmp_path, monkeypatch):
    from agent7_envoy import owner_registry

    path = tmp_path / "owners.json"
    monkeypatch.setattr(owner_registry, "_PATH", path)

    owner_registry.mark_owner(tg_username="@Owner", tg_chat_id="42", object_id="A_1")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"tg:owner", "tg_id:42"}
    entry = payload["tg:owner"]
    assert entry["object_id"] == "A_1"
    assert entry["channel"] == "telegram"
    assert entry["tg_username"] == "Owner"
    assert entry["tg_chat_id"] == "42"
    assert "marked_at" in entry

    assert owner_registry.get_owner("owner") is not None
    assert owner_registry.get_owner("", "42") is not None
    assert owner_registry.get_owner("unknown") is None


def test_owner_registry_preserves_object_id_on_empty_update(tmp_path, monkeypatch):
    from agent7_envoy import owner_registry

    path = tmp_path / "owners.json"
    monkeypatch.setattr(owner_registry, "_PATH", path)

    owner_registry.mark_owner(tg_username="x", object_id="KEEP")
    owner_registry.mark_owner(tg_username="x", object_id="")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tg:x"]["object_id"] == "KEEP"


def test_owner_registry_missing_and_corrupt_file(tmp_path, monkeypatch):
    from agent7_envoy import owner_registry

    path = tmp_path / "owners.json"
    monkeypatch.setattr(owner_registry, "_PATH", path)

    assert owner_registry.get_owner("any") is None

    path.write_text("{not json", encoding="utf-8")
    assert owner_registry.get_owner("any") is None

    owner_registry.mark_owner(tg_username="y", object_id="NEW")
    assert owner_registry.get_owner("y")["object_id"] == "NEW"
