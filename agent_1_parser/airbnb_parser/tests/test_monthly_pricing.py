import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monthly_pricing import (
    collect_monthly_prices,
    collect_monthly_prices_parallel,
    extrapolate_price,
    iter_months_ahead,
    longest_available_segment,
    month_bounds,
    price_for_month,
)


def days_range(start: date, end: date, available: bool = True) -> dict[date, bool]:
    out = {}
    d = start
    while d <= end:
        out[d] = available
        d += timedelta(days=1)
    return out


class TestMonthBounds(unittest.TestCase):
    def test_september(self):
        self.assertEqual(month_bounds(2026, 9), (date(2026, 9, 1), date(2026, 9, 30)))

    def test_february_leap(self):
        self.assertEqual(month_bounds(2028, 2)[1], date(2028, 2, 29))

    def test_iter_months_wraps_year(self):
        months = iter_months_ahead(date(2026, 11, 15), 4)
        self.assertEqual(months, [(2026, 12), (2027, 1), (2027, 2), (2027, 3)])


class TestSegments(unittest.TestCase):
    def test_longest_segment_from_spec_example(self):
        # Сентябрь: заняты 1-16, свободны 17-30 (14 дней)
        avail = days_range(date(2026, 9, 1), date(2026, 9, 16), False)
        avail.update(days_range(date(2026, 9, 17), date(2026, 9, 30), True))
        seg = longest_available_segment(avail, 2026, 9)
        self.assertEqual(seg, (date(2026, 9, 17), date(2026, 9, 30)))

    def test_picks_longest_of_multiple(self):
        avail = days_range(date(2026, 9, 1), date(2026, 9, 30), False)
        avail.update(days_range(date(2026, 9, 2), date(2026, 9, 5), True))    # 4 дня
        avail.update(days_range(date(2026, 9, 10), date(2026, 9, 20), True))  # 11 дней
        seg = longest_available_segment(avail, 2026, 9)
        self.assertEqual(seg, (date(2026, 9, 10), date(2026, 9, 20)))

    def test_no_available_days(self):
        avail = days_range(date(2026, 9, 1), date(2026, 9, 30), False)
        self.assertIsNone(longest_available_segment(avail, 2026, 9))


class TestExtrapolation(unittest.TestCase):
    def test_spec_example(self):
        # 50 000 THB за 14 дней -> 107 143 -> округление до сотен 107 100
        self.assertEqual(extrapolate_price(50000, 14), 107100)


class TestPriceForMonth(unittest.TestCase):
    def test_monthly_status_when_full_month_priced(self):
        avail = days_range(date(2026, 9, 1), date(2026, 9, 30), True)
        result = price_for_month(lambda a, b: 90000, avail, 2026, 9)
        self.assertEqual(result["status"], "monthly")
        self.assertEqual(result["price"], 90000)
        self.assertEqual(result["period_used"], "2026-09-01/2026-09-30")

    def test_prorated_fallback(self):
        # Месяц частично занят: полный месяц не запрашиваем, сразу отрезок
        avail = days_range(date(2026, 9, 1), date(2026, 9, 16), False)
        avail.update(days_range(date(2026, 9, 17), date(2026, 9, 30), True))

        calls = []

        def fetch(check_in, check_out):
            calls.append((check_in, check_out))
            return 50000

        result = price_for_month(fetch, avail, 2026, 9)
        self.assertEqual(result["status"], "prorated")
        self.assertEqual(result["price"], 107100)
        self.assertEqual(result["based_on_days"], 14)
        self.assertEqual(result["period_used"], "2026-09-17/2026-09-30")
        # максимум 2 запроса на месяц; тут занятость известна из календаря — 1 запрос
        self.assertLessEqual(len(calls), 2)

    def test_insufficient_data_short_segment(self):
        # Свободны только 3 дня — меньше минимума 5
        avail = days_range(date(2026, 9, 1), date(2026, 9, 30), False)
        avail.update(days_range(date(2026, 9, 10), date(2026, 9, 12), True))
        result = price_for_month(lambda a, b: 10000, avail, 2026, 9)
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIsNone(result["price"])
        self.assertEqual(result["available_days"], 3)

    def test_insufficient_when_no_price_returned(self):
        avail = days_range(date(2026, 9, 1), date(2026, 9, 30), True)
        result = price_for_month(lambda a, b: None, avail, 2026, 9)
        self.assertEqual(result["status"], "insufficient_data")

    def test_max_two_requests_per_month(self):
        # Календарь пустой (нет данных) — уровень 1 пробуем, цены нет,
        # отрезка нет -> insufficient_data, всего 1 запрос
        calls = []

        def fetch(a, b):
            calls.append((a, b))
            return None

        result = price_for_month(fetch, {}, 2026, 9)
        self.assertEqual(result["status"], "insufficient_data")
        self.assertLessEqual(len(calls), 2)


class TestCollect(unittest.TestCase):
    def test_collect_12_months_keys(self):
        avail = days_range(date(2026, 8, 1), date(2027, 12, 31), True)
        result = collect_monthly_prices(
            lambda a, b: 60000, avail, months_ahead=12, today=date(2026, 7, 12)
        )
        self.assertEqual(len(result), 12)
        self.assertIn("2026-08", result)
        self.assertIn("2027-07", result)
        self.assertTrue(all(v["status"] == "monthly" for v in result.values()))

    def test_collect_respects_months_ahead_param(self):
        result = collect_monthly_prices(lambda a, b: None, {}, months_ahead=6, today=date(2026, 7, 12))
        self.assertEqual(len(result), 6)

    def test_only_missing_skips_cached_prices(self):
        avail = days_range(date(2026, 8, 1), date(2027, 12, 31), True)
        existing = {
            "2026-08": {"price": 408200, "status": "monthly"},
            "2026-09": {"price": None, "status": "insufficient_data"},
        }
        calls = []

        def fetch(a, b):
            calls.append((a, b))
            return 50000

        result = collect_monthly_prices(
            fetch,
            avail,
            months_ahead=3,
            today=date(2026, 7, 12),
            existing=existing,
            only_missing=True,
        )
        self.assertEqual(result["2026-08"]["price"], 408200)
        self.assertEqual(result["2026-09"]["price"], 50000)
        # Август из кэша — полный месяц за август не запрашивали
        self.assertFalse(any(c[0] == date(2026, 8, 1) for c in calls))

    def test_parallel_same_result(self):
        avail = days_range(date(2026, 8, 1), date(2027, 12, 31), True)

        def make_worker():
            return (lambda a, b: 60000), (lambda: None)

        result = collect_monthly_prices_parallel(
            make_worker,
            avail,
            months_ahead=6,
            today=date(2026, 7, 12),
            workers=3,
        )
        self.assertEqual(len(result), 6)
        self.assertTrue(all(v["price"] == 60000 for v in result.values()))


class TestPriceCache(unittest.TestCase):
    def test_listing_id_and_roundtrip(self):
        import tempfile
        from pathlib import Path
        import config
        from price_cache import listing_id_from_url, load_price_cache, save_price_cache

        self.assertEqual(
            listing_id_from_url("https://www.airbnb.ru/rooms/1650574168567264870?x=1"),
            "1650574168567264870",
        )
        with tempfile.TemporaryDirectory() as tmp:
            old_dir, old_en = config.PRICE_CACHE_DIR, config.PRICE_CACHE_ENABLED
            config.PRICE_CACHE_DIR = tmp
            config.PRICE_CACHE_ENABLED = True
            try:
                url = "https://www.airbnb.com/rooms/111"
                save_price_cache(url, {
                    "2026-08": {"price": 10000, "status": "monthly"},
                    "2026-09": {"price": None, "status": "insufficient_data"},
                })
                loaded = load_price_cache(url)
                self.assertEqual(loaded["2026-08"]["price"], 10000)
                self.assertNotIn("2026-09", loaded)
            finally:
                config.PRICE_CACHE_DIR = old_dir
                config.PRICE_CACHE_ENABLED = old_en


if __name__ == "__main__":
    unittest.main()
