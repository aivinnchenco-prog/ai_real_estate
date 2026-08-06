import unittest

from google_sheets import (
    CRM_HEADERS,
    GoogleSheetsWriter,
    _next_object_id,
    _parse_district,
    _parse_object_type_and_location,
    _parse_overview,
    build_row_map,
)


class SheetsHelpersTest(unittest.TestCase):
    def test_next_object_id(self):
        self.assertEqual(_next_object_id(['PHK-0001', 'PHK-0003']), 'PHK-0004')
        self.assertEqual(_next_object_id([]), 'PHK-0001')

    def test_parse_overview_prefers_title_bedrooms(self):
        overview = _parse_overview(
            title='Вилла в районе Bang Tao 4 спальни',
            overview_text='1 спальня, 1 ванная',
            overview_items=['1 спальня', '2 ванные', '8 гостей'],
        )
        self.assertEqual(overview['Спальни'], '4')
        self.assertEqual(overview['Санузлы'], '2')
        self.assertEqual(overview['Гостей'], '8')

    def test_parse_district_strips_bedrooms(self):
        self.assertEqual(
            _parse_district('Вилла в районе Bang Tao 4 спальни'),
            'Bang Tao',
        )

    def test_parse_object_type_and_location(self):
        object_type, location = _parse_object_type_and_location(
            'Дом целиком, Тхаланг, Таиланд'
        )
        self.assertEqual(object_type, 'дом')
        self.assertEqual(location, 'Тхаланг')

    def test_build_row_map_maps_crm_columns(self):
        data = {
            'Название': 'Вилла на пляже',
            'Название_2': 'Дом целиком, Тхаланг, Таиланд',
            'Обзор': '4 спальни, 2,5 ванные, 8 гостей',
            'Обзор_элементы': ['4 спальни', '2,5 ванные', '8 гостей'],
            'Гостей': '8',
            'Цена_отображение': '186 048 ฿ помесячно',
            'Период': '2026-07-01 — 2026-07-31',
            'Описание': 'Описание объекта',
            'Удобства': 'Бассейн, Wi-Fi',
            'Изображения': ['http://img/1.jpg', 'http://img/2.jpg'],
        }
        row_map = build_row_map(
            url='https://airbnb.ru/rooms/123',
            data=data,
            manager='Иван',
            drive_folder='https://drive.google.com/folder/abc',
            object_id='PHK-0099',
            photo_count=2,
            extra_fields={'Текст для FB': 'FB text', 'Текст для TG': 'TG text'},
        )
        self.assertEqual(row_map['ID'], 'PHK-0099')
        self.assertEqual(row_map['Спальни'], '4')
        self.assertEqual(row_map['Исходная цена'], '186 048 ฿ помесячно')
        self.assertEqual(row_map['Цена для поста'], '186 048 ฿ помесячно')
        self.assertEqual(row_map['Текст для FB'], 'FB text')
        self.assertEqual(row_map['Заметки'], 'Менеджер: Иван')

        headers = ['ID', 'Заголовок', 'Текст для FB', 'Текст для TG', 'Исходная цена']
        writer = GoogleSheetsWriter()
        row = writer._row_for_sheet(row_map, headers)
        self.assertEqual(row[0], 'PHK-0099')
        self.assertEqual(row[headers.index('Текст для FB')], 'FB text')
        self.assertEqual(row[headers.index('Текст для TG')], 'TG text')

    def test_build_row_map_rejects_rub_price(self):
        data = {
            'Название': 'Test',
            'Цена_отображение': '539 024 RUB помесячно',
            'Цена': '539024',
        }
        row_map = build_row_map(url='https://airbnb.ru/rooms/1', data=data, object_id='PHK-0001')
        self.assertEqual(row_map['Исходная цена'], '')
        self.assertEqual(row_map['Цена для поста'], '')

    def test_build_row_map_rejects_fake_dollar_amount(self):
        data = {
            'Название': 'Test',
            'Цена_строка': '$539024',
            'Цена': '539024',
        }
        row_map = build_row_map(url='https://airbnb.ru/rooms/1', data=data, object_id='PHK-0001')
        self.assertEqual(row_map['Исходная цена'], '')


if __name__ == '__main__':
    unittest.main()
