from availability_service.app.target_column_order import (
    LOCKED_MONTH_COLUMNS,
    LOCKED_TARGET_COLUMN_ORDER,
    LOCKED_TECHNICAL_COLUMNS,
    locked_columns_present,
    validate_locked_target_schema,
)


def test_locked_column_order_months_then_technical():
    assert LOCKED_TARGET_COLUMN_ORDER[:12] == LOCKED_MONTH_COLUMNS
    assert LOCKED_TARGET_COLUMN_ORDER[12:] == LOCKED_TECHNICAL_COLUMNS
    assert LOCKED_TARGET_COLUMN_ORDER[0] == "Sep 26"
    assert LOCKED_TARGET_COLUMN_ORDER[-1] == "Last Error"


def test_locked_columns_present_filters_schema():
    props = {"Sep 26", "Object ID", "Aug 27"}
    assert locked_columns_present(props) == ["Sep 26", "Aug 27", "Object ID"]


def test_validate_locked_target_schema_pass():
    props = {name: {"type": "rich_text"} for name in LOCKED_TARGET_COLUMN_ORDER}
    props["Object ID"] = {"type": "title"}
    ok, msg = validate_locked_target_schema(props)
    assert ok is True
    assert msg == "PASS"


def test_validate_locked_target_schema_fail_extra():
    props = {name: {} for name in LOCKED_TARGET_COLUMN_ORDER}
    props["Extra"] = {"type": "rich_text"}
    ok, msg = validate_locked_target_schema(props)
    assert ok is False
    assert "unexpected" in msg
