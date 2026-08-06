import unittest
from unittest.mock import patch

from ai_extract import AiExtractor


class AiExtractTest(unittest.TestCase):
    def test_collect_price_snippets_from_html(self):
        html = (
            'aria-label="5 670 $ USD помесячно, исходная цена 7 314 $ USD" '
            '"displayPrice":"539 024 RUB"'
        )
        snippets = AiExtractor()._collect_price_snippets(html)
        self.assertTrue(any('USD' in s for s in snippets))

    @patch.object(AiExtractor, 'is_enabled', return_value=True)
    @patch('gemini_llm.generate')
    def test_extract_price_parses_claude_json(self, mock_generate, _enabled):
        mock_generate.return_value = (
            '{"amount":"5670","display":"5 670 $ USD помесячно",'
            '"currency":"USD","confidence":"high"}'
        )

        extractor = AiExtractor()
        result = extractor.extract_price(
            url='https://airbnb.ru/rooms/1?currency=USD',
            url_dates={
                'check_in': '2026-07-01',
                'check_out': '2026-07-31',
                'period': '2026-07-01 — 2026-07-31',
                'currency': 'USD',
            },
            page_source='aria-label="5 670 $ USD помесячно"',
            listing_title='Villa',
        )
        self.assertEqual(result['Цена'], '5670')
        self.assertIn('USD', result['Цена_отображение'])
        self.assertEqual(result['Цена_источник'], 'ai')
        mock_generate.assert_called_once()

    @patch.object(AiExtractor, 'is_enabled', return_value=True)
    @patch('gemini_llm.generate')
    def test_extract_price_rejects_rub_display(self, mock_generate, _enabled):
        mock_generate.return_value = (
            '{"amount":"539024","display":"539 024 RUB","currency":"USD","confidence":"high"}'
        )

        result = AiExtractor().extract_price(
            url='https://airbnb.ru/rooms/1?currency=USD',
            url_dates={'currency': 'USD'},
            page_source='',
        )
        self.assertEqual(result, {})


if __name__ == '__main__':
    unittest.main()
