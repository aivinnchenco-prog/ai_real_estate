from datetime import date, timedelta

from availability_service.app.models import AvailabilityStatus, calendar_day
from availability_service.app.models import DailyAvailabilityStatus, SourceKind
from availability_service.app.stay_matcher import (
    add_calendar_months,
    check_stay_availability,
    iter_stay_days,
)
from availability_service.app.status_mapper import (
    AirbnbMonthPolicy,
    build_month_availabilities,
    evaluate_month_calendar_status,
)
from availability_service.app.models import MonthWindowItem


def _range_days(start: date, end: date, available: bool = True) -> dict[date, bool]:
    out: dict[date, bool] = {}
    day = start
    while day < end:
        out[day] = available
        day += timedelta(days=1)
    return out


def _cal_list(cal: dict[date, bool]) -> list:
    return [
        calendar_day(d, available=v, source=SourceKind.AIRBNB, source_state="test")
        for d, v in cal.items()
    ]


def test_sep_15_one_month_all_available():
    check_in = date(2026, 9, 15)
    check_out = add_calendar_months(check_in, 1)
    assert check_out == date(2026, 10, 15)
    cal = _range_days(check_in, check_out, True)
    result = check_stay_availability("A_001", check_in, 1, cal)
    assert result.status == AvailabilityStatus.AVAILABLE
    assert result.check_out == check_out
    assert not result.blocked_dates
    assert not result.unknown_dates


def test_sep_15_three_months_one_blocked_in_oct():
    check_in = date(2026, 9, 15)
    check_out = add_calendar_months(check_in, 3)
    assert check_out == date(2026, 12, 15)
    cal = _range_days(check_in, check_out, True)
    cal[date(2026, 10, 20)] = False
    result = check_stay_availability("A_001", check_in, 3, cal)
    assert result.status == AvailabilityStatus.UNAVAILABLE
    assert date(2026, 10, 20) in result.blocked_dates


def test_sep_15_three_months_missing_nov_days():
    check_in = date(2026, 9, 15)
    check_out = add_calendar_months(check_in, 3)
    cal = _range_days(check_in, check_out, True)
    for missing in (date(2026, 11, 1), date(2026, 11, 2)):
        del cal[missing]
    result = check_stay_availability("A_001", check_in, 3, cal)
    assert result.status == AvailabilityStatus.UNKNOWN
    assert result.unknown_dates
    assert not result.blocked_dates


def test_no_price_all_calendar_available_stay_available():
    check_in = date(2026, 9, 15)
    check_out = add_calendar_months(check_in, 1)
    cal = _range_days(check_in, check_out, True)
    result = check_stay_availability("A_001", check_in, 1, cal)
    assert result.status == AvailabilityStatus.AVAILABLE


def test_price_irrelevant_one_blocked_day():
    check_in = date(2026, 9, 15)
    check_out = add_calendar_months(check_in, 1)
    cal = _range_days(check_in, check_out, True)
    cal[date(2026, 9, 20)] = False
    result = check_stay_availability("A_001", check_in, 1, cal)
    assert result.status == AvailabilityStatus.UNAVAILABLE


def test_jan_31_one_month_non_leap():
    check_in = date(2025, 1, 31)
    check_out = add_calendar_months(check_in, 1)
    assert check_out == date(2025, 2, 28)


def test_jan_31_one_month_leap_year():
    check_in = date(2024, 1, 31)
    check_out = add_calendar_months(check_in, 1)
    assert check_out == date(2024, 2, 29)


def test_mar_31_one_month():
    check_in = date(2026, 3, 31)
    check_out = add_calendar_months(check_in, 1)
    assert check_out == date(2026, 4, 30)


def test_check_out_day_not_in_stay_days():
    check_in = date(2026, 9, 15)
    check_out = add_calendar_months(check_in, 3)
    stay = list(iter_stay_days(check_in, check_out))
    assert stay[-1] == date(2026, 12, 14)
    assert check_out not in stay


def test_month_display_status_does_not_affect_stay_matcher():
    """Month summary AVAILABLE but exact stay hits blocked day → UNAVAILABLE."""
    check_in = date(2026, 9, 15)
    check_out = add_calendar_months(check_in, 1)
    cal_dict = _range_days(check_in, check_out, True)
    cal_dict[date(2026, 9, 20)] = False
    month_cal = _range_days(date(2026, 9, 1), date(2026, 10, 1), False)
    for i in range(15):
        month_cal[date(2026, 9, 1) + timedelta(days=i)] = True
    month_status = evaluate_month_calendar_status(
        month_cal, 2026, 9, AirbnbMonthPolicy.CONTIGUOUS_STAY_REQUIRED, min_contiguous_days=5
    )
    assert month_status == AvailabilityStatus.AVAILABLE

    stay_result = check_stay_availability("A_001", check_in, 1, cal_dict)
    assert stay_result.status == AvailabilityStatus.UNAVAILABLE

    window = [MonthWindowItem(2026, 9, "2026-09", "Sep 26")]
    month_rows = build_month_availabilities(
        window,
        _cal_list(month_cal),
        {"2026-09": {"price": 50000, "status": "monthly"}},
        AirbnbMonthPolicy.CONTIGUOUS_STAY_REQUIRED,
    )
    assert month_rows[0].status == AvailabilityStatus.AVAILABLE
    assert stay_result.status == AvailabilityStatus.UNAVAILABLE
