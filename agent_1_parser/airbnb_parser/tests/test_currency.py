import unittest
from unittest import mock

from airbnb_parser import AirbnbParser
from airbnb_url import normalize_airbnb_url, resolve_currency


class CurrencyResolutionTest(unittest.TestCase):
    def setUp(self):
        self.parser = AirbnbParser()

    def test_preserves_usd_from_url(self):
        url = (
            'https://www.airbnb.ru/rooms/123'
            '?check_in=2026-07-01&check_out=2026-07-31&currency=USD'
        )
        self.assertEqual(resolve_currency(url), 'USD')
        prepared = normalize_airbnb_url(url)
        self.assertIn('currency=USD', prepared)
        self.assertNotIn('currency=THB', prepared)

    def test_adds_default_currency_when_missing(self):
        url = 'https://www.airbnb.ru/rooms/123?check_in=2026-07-01&check_out=2026-07-31'
        prepared = normalize_airbnb_url(url)
        self.assertIn('currency=', prepared)
        self.assertNotIn('currency=RUB', prepared)

    def test_adds_currency_when_empty_param(self):
        url = 'https://www.airbnb.com/rooms/123?currency=&check_in=2026-07-01'
        prepared = normalize_airbnb_url(url)
        self.assertRegex(prepared, r'currency=(THB|USD)')
        self.assertIn('airbnb.ru', prepared)

    def test_uppercases_currency_in_url(self):
        url = 'https://www.airbnb.ru/rooms/123?currency=usd&check_in=2026-07-01'
        prepared = normalize_airbnb_url(url)
        self.assertIn('currency=USD', prepared)
        self.assertNotIn('currency=usd', prepared)

    def test_rejects_rub_when_target_usd(self):
        self.parser._target_currency = 'USD'
        self.assertFalse(self.parser._accept_price_display('539 024 RUB помесячно'))
        self.assertTrue(self.parser._accept_price_display('5 670 $ USD помесячно'))
        self.assertFalse(self.parser._accept_price_display('539024'))

    def test_extract_usd_from_aria_label_html(self):
        self.parser._target_currency = 'USD'
        html = (
            'aria-label="5&nbsp;670&nbsp;$&nbsp;USD помесячно, '
            'исходная цена 7&nbsp;314&nbsp;$&nbsp;USD"'
        )
        amount, display = self.parser._extract_price_from_html(html)
        self.assertEqual(amount, '5670')
        self.assertNotIn('7314', display)
        self.assertIn('USD', display)
        self.assertNotIn('RUB', display)

    def test_rejects_rub_when_target_thb(self):
        self.parser._target_currency = 'THB'
        self.assertFalse(self.parser._accept_price_display('417 852 RUB'))
        self.assertTrue(self.parser._accept_price_display('186 048 ฿ помесячно'))

    def test_no_stale_rub_after_failed_final_extract(self):
        from unittest.mock import patch

        self.parser._target_currency = 'USD'
        url_dates = {
            'currency': 'USD',
            'period': '2026-07-01 — 2026-07-31',
            'check_in': '2026-07-01',
            'check_out': '2026-07-31',
        }
        data = {
            'Цена': '539024',
            'Цена_отображение': '',
            'Цена_строка': '$539024 (2026-07-01 — 2026-07-31)',
        }
        self.parser._clear_price_fields(data)
        with patch.object(self.parser, '_extract_price_from_html', return_value=('', '')):
            with patch.object(self.parser, '_extract_price_from_dom', return_value={}):
                with patch('airbnb_parser.config.ENABLE_AI_PRICE_FALLBACK', False):
                    result = self.parser._extract_final_price('no price', data, url_dates)
        self.assertEqual(result, {})
        self.assertNotIn('Цена', data)

    def test_rejects_fake_dollar_prefix_on_rub_amount(self):
        self.parser._target_currency = 'USD'
        fields = self.parser._validate_price_fields({
            'Цена': '539024',
            'Цена_отображение': '$539024',
            'Цена_строка': '$539024',
        })
        self.assertEqual(fields, {})

    def test_extract_final_price_prefers_currency_from_url(self):
        url = (
            'https://www.airbnb.ru/rooms/123'
            '?check_in=2026-07-01&check_out=2026-07-31&currency=THB'
        )
        url_dates = {
            'currency': 'THB',
            'period': '2026-07-01 — 2026-07-31',
            'check_in': '2026-07-01',
            'check_out': '2026-07-31',
        }
        html = (
            '<span aria-label="539 024 RUB помесячно"></span>'
            '<span aria-label="186 048 ฿ помесячно"></span>'
        )
        self.parser._target_currency = 'THB'
        with mock.patch('airbnb_parser.config.ENABLE_AI_PRICE_FALLBACK', False):
            result = self.parser._extract_final_price(html, {}, url_dates, url)
        self.assertEqual(result.get('Цена'), '186048')
        self.assertIn('฿', result.get('Цена_отображение', ''))
        self.assertNotIn('RUB', result.get('Цена_отображение', ''))


if __name__ == '__main__':
    unittest.main()
