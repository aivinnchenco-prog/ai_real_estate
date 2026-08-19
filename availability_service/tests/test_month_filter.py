"""Unit tests for website month-period matching (V1)."""
from availability_service.app.month_display import MonthDisplayStatus
from availability_service.app.month_filter import matches_month_period


def test_all_fully_available_matches():
    assert matches_month_period(
        [
            MonthDisplayStatus.FULLY_AVAILABLE,
            MonthDisplayStatus.FULLY_AVAILABLE,
            MonthDisplayStatus.FULLY_AVAILABLE,
        ]
    )


def test_partial_in_period_no_match():
    assert not matches_month_period(
        [
            MonthDisplayStatus.FULLY_AVAILABLE,
            MonthDisplayStatus.PARTIAL,
            MonthDisplayStatus.FULLY_AVAILABLE,
        ]
    )


def test_unavailable_in_period_no_match():
    assert not matches_month_period(
        [
            MonthDisplayStatus.FULLY_AVAILABLE,
            MonthDisplayStatus.UNAVAILABLE,
            MonthDisplayStatus.FULLY_AVAILABLE,
        ]
    )


def test_unknown_in_period_no_match():
    assert not matches_month_period(
        [
            MonthDisplayStatus.FULLY_AVAILABLE,
            MonthDisplayStatus.UNKNOWN,
            MonthDisplayStatus.FULLY_AVAILABLE,
        ]
    )


def test_string_status_values():
    assert matches_month_period(["FULLY_AVAILABLE", "FULLY_AVAILABLE"])
    assert not matches_month_period(["FULLY_AVAILABLE", "PARTIAL"])


def test_empty_period_no_match():
    assert not matches_month_period([])
