import unittest
from unittest.mock import patch

from enrich import ListingEnricher


class EnrichTest(unittest.TestCase):
    def test_fallback_returns_fb_and_tg(self):
        enricher = ListingEnricher()
        listing = {
            'Название': 'Вилла Bang Tao',
            'Обзор': '4 спальни, 2 ванные',
            'Цена_строка': '186 048 ฿ помесячно',
            'Период': '2026-07-01 — 2026-07-31',
            'Особенности': 'Бассейн, вид на море',
            'Описание': 'Просторная вилла у пляжа.',
        }
        result = enricher.fallback_texts(listing, 'PHK-0007', 'https://drive/folder')
        self.assertTrue(result['Текст для FB'])
        self.assertTrue(result['Текст для TG'])
        self.assertIn('PHK-0007', result['Текст для FB'])
        self.assertIn('186 048', result['Текст для TG'])
        self.assertIn('#Пхукет', result['Текст для TG'])

    def test_enrich_without_api_uses_fallback(self):
        enricher = ListingEnricher()
        with patch.object(enricher, 'is_enabled', return_value=False):
            result = enricher.enrich({'Название': 'Test'}, 'PHK-0001')
        self.assertIn('Текст для FB', result)
        self.assertIn('Текст для TG', result)


if __name__ == '__main__':
    unittest.main()
