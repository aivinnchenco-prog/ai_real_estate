from availability_service.app.notion_reader import (
    compare_month_schema,
    map_source_fields,
    normalize_source,
    resolve_calendar_check_url,
    sort_month_columns,
    sort_technical_columns,
    target_display_column_order,
)
from availability_service.app.models import SourceKind, build_month_window, get_effective_window_start, WindowStartMode
from datetime import date


def test_source_mapping_from_live_like_schema():
    properties = {
        "Название объекта": {"type": "title", "id": "title"},
        "Объект ID": {"type": "rich_text", "id": "oid"},
        "Источник объявления": {"type": "url", "id": "srcurl"},
        "Календарь": {"type": "url", "id": "cal"},
        "Фото": {"type": "url", "id": "photo"},
        "Район": {"type": "rich_text", "id": "dist"},
    }
    mapping = map_source_fields(properties)
    assert mapping.object_id.status == "mapped"
    assert mapping.object_id.property_name == "Объект ID"
    assert mapping.name.property_name == "Название объекта"
    assert mapping.source_url.property_name == "Источник объявления"
    assert mapping.source.status == "unmapped"


def test_ambiguous_source_url_not_guessed():
    properties = {
        "Источник объявления": {"type": "url", "id": "a"},
        "Source URL": {"type": "url", "id": "b"},
    }
    mapping = map_source_fields(properties)
    assert mapping.source_url.status == "ambiguous"
    assert mapping.source_url.property_name is None


def test_normalize_source_from_url_and_object_id():
    assert normalize_source(source_url="https://www.airbnb.com/rooms/1") == SourceKind.AIRBNB
    assert normalize_source(source_url="https://www.facebook.com/marketplace/item/1") == SourceKind.FACEBOOK
    assert normalize_source(object_id="A_20260814_001") == SourceKind.AIRBNB
    assert normalize_source(object_id="F_20260814_001") == SourceKind.FACEBOOK
    assert normalize_source(source_url="https://example.com/x") == SourceKind.UNKNOWN


def test_month_schema_match_pass():
    start = get_effective_window_start(date(2026, 8, 14), WindowStartMode.NEXT_MONTH)
    window = build_month_window(start, months=12)
    notion_cols = [
        "Sep 26", "Oct 26", "Nov 26", "Dec 26",
        "Jan 27", "Feb 27", "Mar 27", "Apr 27",
        "May 27", "Jun 27", "Jul 27", "Aug 27",
    ]
    ok, msg = compare_month_schema(window, notion_cols)
    assert ok is True
    assert msg == "PASS"
    assert sort_month_columns(notion_cols) == notion_cols


def test_month_schema_match_fail():
    start = get_effective_window_start(date(2026, 8, 14), WindowStartMode.CURRENT_MONTH)
    window = build_month_window(start, months=12)
    notion_cols = ["Sep 26", "Oct 26"]
    ok, msg = compare_month_schema(window, notion_cols)
    assert ok is False
    assert msg.startswith("FAIL:")


def test_resolve_calendar_check_url_airbnb_fallback():
    assert resolve_calendar_check_url(
        calendar_url="",
        source_url="https://www.airbnb.com/rooms/1",
        source=SourceKind.AIRBNB,
    ) == "https://www.airbnb.com/rooms/1"
    assert resolve_calendar_check_url(
        calendar_url="https://docs.google.com/spreadsheets/d/x",
        source_url="https://www.airbnb.com/rooms/1",
        source=SourceKind.AIRBNB,
    ) == "https://docs.google.com/spreadsheets/d/x"


def test_target_display_column_order_months_then_technical():
    month_cols = ["Oct 26", "Sep 26", "Nov 26"]
    tech_cols = ["Refresh Tier", "Object ID", "Объект", "Source"]
    order = target_display_column_order(month_cols, tech_cols)
    assert order[:3] == ["Sep 26", "Oct 26", "Nov 26"]
    assert order[3:] == ["Object ID", "Объект", "Source", "Refresh Tier"]


def test_sort_technical_columns_canonical_order():
    names = ["Last Error", "Object ID", "Source", "Объект"]
    assert sort_technical_columns(names) == [
        "Object ID", "Объект", "Source", "Last Error",
    ]
