import json
import os
import unittest

from airbnb_parser import AirbnbParser
from google_sheets import build_row_map


SAMPLE_JSON = os.path.join(os.path.dirname(__file__), '..', 'Samples', 'Details.json')


class ParserSampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(SAMPLE_JSON):
            raise unittest.SkipTest('Samples/Details.json not found')

    def test_parse_saved_json_extracts_core_fields(self):
        parser = AirbnbParser(headless=True)
        details = parser.parse_saved_json(
            SAMPLE_JSON,
            url_dates={
                'check_in': '2026-07-01',
                'check_out': '2026-07-31',
                'period': '2026-07-01 — 2026-07-31',
            },
            page_source=(
                '<span aria-label="186&nbsp;048&nbsp;฿ помесячно, '
                'исходная цена 240&nbsp;000&nbsp;฿"></span>'
            ),
        )
        self.assertIn('Название', details)
        self.assertTrue(details['Название'])
        self.assertIn('Описание', details)
        self.assertIn('Удобства', details)
        self.assertTrue(details.get('Изображения'))
        self.assertEqual(len(details.get('Изображения', [])), 66)
        self.assertEqual(details.get('Цена'), '186048')
        self.assertIn('฿', details.get('Цена_отображение', ''))

    def test_parsed_data_maps_to_crm_schema(self):
        parser = AirbnbParser(headless=True)
        details = parser.parse_saved_json(SAMPLE_JSON)
        row_map = build_row_map(
            url='https://airbnb.ru/rooms/1641680797798472453',
            data=details,
            object_id='PHK-TEST',
        )
        self.assertTrue(row_map.get('Заголовок'))
        self.assertTrue(row_map.get('Исходное описание'))
        self.assertTrue(row_map.get('Все удобства'))


if __name__ == '__main__':
    unittest.main()
