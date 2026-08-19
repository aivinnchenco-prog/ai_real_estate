from datetime import date, datetime

from availability_service.app.models import (
    WindowStartMode,
    build_month_window,
    get_availability_window,
    get_effective_window_start,
)


def _keys(items):
    return [item.key for item in items]


def _names(items):
    return [item.display_name for item in items]


def test_effective_start_aug_2026_next_month():
    start = get_effective_window_start(date(2026, 8, 14), WindowStartMode.NEXT_MONTH)
    window = build_month_window(start, months=12)
    assert start == date(2026, 9, 1)
    assert _names(window) == [
        "Sep 26", "Oct 26", "Nov 26", "Dec 26",
        "Jan 27", "Feb 27", "Mar 27", "Apr 27",
        "May 27", "Jun 27", "Jul 27", "Aug 27",
    ]


def test_effective_start_sep_2026_next_month():
    start = get_effective_window_start(date(2026, 9, 1), WindowStartMode.NEXT_MONTH)
    window = build_month_window(start, months=12)
    assert start == date(2026, 10, 1)
    assert _names(window)[0] == "Oct 26"
    assert _names(window)[-1] == "Sep 27"


def test_effective_start_dec_2026_next_month():
    start = get_effective_window_start(date(2026, 12, 15), WindowStartMode.NEXT_MONTH)
    window = build_month_window(start, months=12)
    assert start == date(2027, 1, 1)
    assert _names(window)[0] == "Jan 27"
    assert _names(window)[-1] == "Dec 27"


def test_effective_start_current_month():
    start = get_effective_window_start(date(2026, 8, 14), WindowStartMode.CURRENT_MONTH)
    assert start == date(2026, 8, 1)
    window = build_month_window(start, months=12)
    assert _names(window)[0] == "Aug 26"
    assert _names(window)[-1] == "Jul 27"


def test_sep_2026_to_aug_2027():
    window = build_month_window(date(2026, 9, 1), months=12)
    assert len(window) == 12
    assert window[0].year == 2026 and window[0].month == 9
    assert window[-1].year == 2027 and window[-1].month == 8
    assert _keys(window)[0] == "2026-09"
    assert _keys(window)[-1] == "2027-08"
    assert window[0].display_name == "Sep 26"
    assert window[-1].display_name == "Aug 27"


def test_oct_2026_to_sep_2027():
    window = build_month_window(date(2026, 10, 14), months=12)
    assert _keys(window)[0] == "2026-10"
    assert _keys(window)[-1] == "2027-09"
    assert window[0].display_name == "Oct 26"
    assert window[-1].display_name == "Sep 27"


def test_dec_2026_to_nov_2027():
    window = build_month_window(date(2026, 12, 1), months=12)
    assert _keys(window)[0] == "2026-12"
    assert _keys(window)[-1] == "2027-11"
    assert window[0].display_name == "Dec 26"
    assert window[-1].display_name == "Nov 27"


def test_year_transition_included():
    window = build_month_window(date(2026, 12, 31), months=12)
    years = {item.year for item in window}
    assert years == {2026, 2027}
    assert any(item.key == "2027-01" for item in window)


def test_leap_year_start_is_safe():
    window = build_month_window(date(2024, 2, 29), months=12)
    assert len(window) == 12
    assert window[0].key == "2024-02"
    assert window[1].key == "2024-03"
    assert window[-1].key == "2025-01"
    assert window[-1].display_name == "Jan 25"


def test_fixed_sep_2026_aug_2027_window():
    window = get_availability_window(WindowStartMode.SEP_2026_AUG_2027)
    assert _names(window) == [
        "Sep 26", "Oct 26", "Nov 26", "Dec 26",
        "Jan 27", "Feb 27", "Mar 27", "Apr 27",
        "May 27", "Jun 27", "Jul 27", "Aug 27",
    ]
    assert window[0].year == 2026 and window[0].month == 9
    assert window[-1].year == 2027 and window[-1].month == 8


def test_window_calendar_bounds_sep_2026_aug_2027():
    from availability_service.app.models import window_calendar_bounds

    window = get_availability_window(WindowStartMode.SEP_2026_AUG_2027)
    start, end = window_calendar_bounds(window)
    assert start == date(2026, 9, 1)
    assert end == date(2027, 8, 31)
    starts = [
        date(2026, 1, 1),
        date(2026, 8, 14),
        date(2026, 9, 1),
        date(2027, 2, 28),
    ]
    for start in starts:
        window = build_month_window(start, months=12)
        assert len(window) == 12
        keys = _keys(window)
        assert len(set(keys)) == 12
