import os
import re
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

import config
from CustomLogger import logger

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

# A–G: идентификация, H–O: ключевые характеристики объекта
_CRM_PREFIX = [
    'ID',
    'Источник',
    'Тип сделки',
    'Статус',
    'Ссылка на объявление',
    'Папка фото (Drive)',
    'Кол-во фото',
]
_CRM_PROPERTY = [
    'Район',
    'Локация / посёлок',
    'Спальни',
    'Санузлы',
    'Гостей',
    'Кроватей',
    'Площадь, м²',
    'Цена для поста',
]
_CRM_REST = [
    'Транскрипт переписки',
    'FB: статус публ.',
    'FB: ссылка поста',
    'TG: статус публ.',
    'TG: ссылка поста',
    'Дата 1-го контакта',
    'Дата согласия',
    'Дата публикации',
    'Тип контрагента',
    'Имя контакта',
    'WhatsApp',
    'Исходная цена',
    'Комиссия, %',
    'Цена год-контракт',
    'Депозит',
    'Электричество',
    'Прочие условия',
    'Заголовок',
    'Тип объекта',
    'Доступные даты',
    'Бассейн',
    'Вид',
    'Парковка',
    'Можно с кошками',
    'Можно с собаками',
    'Меблировка',
    'Ключевые фишки (УТП)',
    'Что рядом',
    'Текст для FB',
    'Текст для TG',
    'Год постройки',
    'Безопасность',
    'Все удобства',
    'Исходное описание',
    'Заметки',
]
CRM_HEADERS = _CRM_PREFIX + _CRM_PROPERTY + _CRM_REST


def _sanitize_price_for_sheet(price_display, price_value):
    text = (price_display or price_value or '').strip()
    if not text:
        return ''
    if re.search(r'RUB|₽|руб', text, re.I):
        return ''
    compact = text.replace(' ', '').replace('\xa0', '')
    if re.fullmatch(r'[$฿]\d{3,}.*', compact) and not re.search(r'USD|THB|฿\s|помесячно', text, re.I):
        return ''
    if re.fullmatch(r'\d[\d\s]*', text.replace('\xa0', ' ')) and not re.search(r'USD|THB|฿|\$', text):
        return ''
    return text


def build_row_map(url, data, manager='', drive_folder='', object_id='', photo_count=None, extra_fields=None):
    images = data.get('Изображения', []) or []
    title = data.get('Название', '').strip()
    name_2 = data.get('Название_2', '').strip()
    overview = _parse_overview(
        title=title,
        overview_text=data.get('Обзор', ''),
        overview_items=data.get('Обзор_элементы'),
        guests_capacity=data.get('Гостей', ''),
    )
    object_type, location = _parse_object_type_and_location(name_2)
    district = _parse_district(title)
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    price_display = data.get('Цена_отображение') or data.get('Цена_строка', '')
    price_value = data.get('Цена', '')
    period = data.get('Период', '')
    price_for_sheet = _sanitize_price_for_sheet(price_display, price_value)

    row_map = {
        'ID': object_id,
        'Источник': config.SOURCE_AIRBNB,
        'Тип сделки': config.DEAL_TYPE,
        'Статус': config.STATUS_FOUND,
        'Ссылка на объявление': url,
        'Папка фото (Drive)': drive_folder,
        'Кол-во фото': str(photo_count if photo_count is not None else len(images)),
        'Дата 1-го контакта': now,
        'Исходная цена': price_for_sheet,
        'Заголовок': title,
        'Тип объекта': object_type,
        'Район': district,
        'Локация / посёлок': location or title,
        'Доступные даты': period,
        'Цена для поста': price_for_sheet,
        'Все удобства': data.get('Удобства', ''),
        'Исходное описание': data.get('Описание', ''),
        'Заметки': _build_notes(manager),
        **overview,
    }
    if extra_fields:
        row_map.update(extra_fields)
    return row_map


def _next_object_id(existing_ids):
    prefix = f'{config.OBJECT_ID_PREFIX}-'
    pattern = re.compile(rf'^{re.escape(config.OBJECT_ID_PREFIX)}-(\d+)$')

    max_num = 0
    for object_id in existing_ids:
        match = pattern.match(str(object_id).strip())
        if match:
            max_num = max(max_num, int(match.group(1)))

    return f'{prefix}{max_num + 1:04d}'


_OBJECT_TYPE_PREFIXES = (
    ('Вилла целиком', 'вилла'),
    ('Дом целиком', 'дом'),
    ('Таунхаус целиком', 'таунхаус'),
    ('Квартира целиком', 'квартира'),
    ('Жилье целиком', 'жильё'),
)

_COUNTRY_NAMES = {'таиланд', 'thailand'}


def _split_overview_segments(text):
    if not text:
        return []
    return [part.strip() for part in re.split(r'[,·]', text) if part.strip()]


def _extract_from_segments(segments, patterns):
    for segment in segments:
        segment_lower = segment.lower().replace('ё', 'е')
        for pattern in patterns:
            match = re.search(pattern, segment_lower, re.IGNORECASE)
            if match:
                return match.group(1)
    return ''


def _parse_bedrooms(title, overview_text='', overview_items=None):
    for source in (title, overview_text):
        if not source:
            continue
        match = re.search(r'(\d+)\s*спальн', source, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r'(\d+)\s*комнат', source, re.IGNORECASE)
        if match:
            return match.group(1)

    segments = list(overview_items or [])
    segments.extend(_split_overview_segments(overview_text))
    return _extract_from_segments(segments, [
        r'(\d+)\s*спальн',
        r'(\d+)\s*комнат',
        r'(\d+)\s*bedrooms?',
    ])


def _parse_overview(title='', overview_text='', overview_items=None, guests_capacity=''):
    segments = list(overview_items or [])
    segments.extend(_split_overview_segments(overview_text))

    guests = str(guests_capacity).strip() if guests_capacity else ''
    if not guests:
        guests = _extract_from_segments(segments, [
            r'(\d+)\s*гост',
            r'(\d+)\s*guests?',
        ])

    return {
        'Спальни': _parse_bedrooms(title, overview_text, overview_items),
        'Санузлы': _extract_from_segments(segments, [
            r'(\d+)\s*санузл',
            r'(\d+)\s*ванн',
            r'(\d+)\s*bathrooms?',
        ]),
        'Гостей': guests,
        'Кроватей': _extract_from_segments(segments, [
            r'(\d+)\s*кроват',
            r'(\d+)\s*beds?',
        ]),
    }


def _parse_object_type_and_location(name_2):
    name_2 = (name_2 or '').strip()
    if not name_2:
        return '', ''

    object_type = ''
    rest = name_2
    for prefix, normalized_type in _OBJECT_TYPE_PREFIXES:
        if name_2.startswith(prefix):
            object_type = normalized_type
            rest = name_2[len(prefix):].lstrip(', ').strip()
            break

    parts = [part.strip() for part in re.split(r'[,·]', rest) if part.strip()]
    location = ''
    for part in parts:
        if part.lower() not in _COUNTRY_NAMES:
            location = part
            break
    if not location and parts:
        location = parts[0]
    if not object_type and parts:
        first = parts[0].lower()
        if first not in _COUNTRY_NAMES:
            object_type = parts[0]

    return object_type, location


def _parse_district(title):
    if not title:
        return ''

    match = re.search(r'районе\s+(.+?)(?:\s+\d+\s*спальн|\s*$)', title, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    for district in ('Bang Tao', 'банг тао', 'Cherngtalay', 'чернг талай', 'Thalang', 'тхаланг', 'Laguna', 'лагуна'):
        if district.lower() in title.lower():
            return district

    return ''


def _build_notes(manager):
    if manager:
        return f'Менеджер: {manager}'
    return ''


class GoogleSheetsWriter:
    def __init__(self):
        self._worksheet = None
        self._spreadsheet = None

    def is_configured(self):
        return bool(
            config.GOOGLE_SPREADSHEET_ID
            and os.path.exists(config.GOOGLE_CREDENTIALS_FILE)
        )

    def _get_spreadsheet(self):
        if self._spreadsheet is not None:
            return self._spreadsheet

        credentials = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_FILE,
            scopes=SCOPES,
        )
        client = gspread.authorize(credentials)
        self._spreadsheet = client.open_by_key(config.GOOGLE_SPREADSHEET_ID)
        return self._spreadsheet

    def _get_worksheet(self):
        if self._worksheet is not None:
            return self._worksheet

        spreadsheet = self._get_spreadsheet()
        self._worksheet = spreadsheet.worksheet(config.GOOGLE_SHEET_NAME)
        return self._worksheet

    def _get_existing_object_ids(self, worksheet):
        values = worksheet.col_values(1)
        return values[1:] if len(values) > 1 else []

    def _collect_existing_ids(self):
        spreadsheet = self._get_spreadsheet()
        ids = []
        for sheet_name in (config.GOOGLE_SHEET_NAME, 'Вход'):
            try:
                worksheet = spreadsheet.worksheet(sheet_name)
                ids.extend(self._get_existing_object_ids(worksheet))
            except gspread.WorksheetNotFound:
                continue
        return ids

    def next_object_id(self):
        return _next_object_id(self._collect_existing_ids())

    def _get_sheet_headers(self, worksheet):
        rows = worksheet.get_all_values()
        if rows and rows[0] and any(str(cell).strip() for cell in rows[0]):
            return rows[0]
        worksheet.update([CRM_HEADERS], 'A1', value_input_option='RAW')
        return list(CRM_HEADERS)

    def _row_for_sheet(self, row_map, headers):
        return [str(row_map.get(header, '')) for header in headers]

    def append_listing(self, url, data, manager='', drive_folder='', object_id=None, photo_count=None, extra_fields=None):
        if not self.is_configured():
            logger.warning('Google Sheets не настроен. Запись в таблицу пропущена.')
            return False

        worksheet = self._get_worksheet()
        if not object_id:
            object_id = _next_object_id(self._collect_existing_ids())

        row_map = build_row_map(
            url=url,
            data=data,
            manager=manager,
            drive_folder=drive_folder,
            object_id=object_id,
            photo_count=photo_count,
            extra_fields=extra_fields,
        )
        headers = self._get_sheet_headers(worksheet)
        row = self._row_for_sheet(row_map, headers)
        worksheet.append_row(row, value_input_option='RAW')
        fb_len = len(str(row_map.get('Текст для FB', '')))
        tg_len = len(str(row_map.get('Текст для TG', '')))
        price_note = row_map.get('Исходная цена', '') or '(пусто)'
        logger.info(
            f'Объявление {object_id} добавлено в лист «{config.GOOGLE_SHEET_NAME}» '
            f'(цена: {price_note}, FB={fb_len} симв., TG={tg_len} симв.).'
        )
        return object_id

    def get_all_listings(self) -> list[dict]:
        if not self.is_configured():
            return []

        worksheet = self._get_worksheet()
        rows = worksheet.get_all_values()
        if len(rows) < 2:
            return []

        headers = rows[0]
        listings = []
        for row in rows[1:]:
            if not row or not str(row[0]).strip():
                continue
            padded = row + [''] * (len(headers) - len(row))
            listings.append(dict(zip(headers, padded)))
        return listings

    def update_listing_fields(self, object_id: str, fields: dict) -> bool:
        if not self.is_configured():
            return False

        worksheet = self._get_worksheet()
        rows = worksheet.get_all_values()
        if len(rows) < 2:
            return False

        headers = rows[0]
        object_id = object_id.strip().upper()
        for row_idx, row in enumerate(rows[1:], start=2):
            if not row or row[0].strip().upper() != object_id:
                continue
            padded = row + [''] * (len(headers) - len(row))
            row_dict = dict(zip(headers, padded))
            row_dict.update(fields)
            new_row = self._row_for_sheet(row_dict, headers)
            worksheet.update(f'A{row_idx}', [new_row], value_input_option='RAW')
            logger.info(f'Объект {object_id} обновлён в таблице.')
            return True
        return False

    def get_listing_by_id(self, object_id: str) -> dict | None:
        object_id = object_id.strip().upper()
        for listing in self.get_all_listings():
            if listing.get('ID', '').strip().upper() == object_id:
                return listing
        return None

    def reorder_worksheet_columns(self):
        if not self.is_configured():
            logger.warning('Google Sheets не настроен.')
            return False

        worksheet = self._get_worksheet()
        rows = worksheet.get_all_values()
        if not rows:
            worksheet.update([CRM_HEADERS], 'A1')
            logger.info('Заголовки CRM записаны в пустой лист.')
            return True

        old_headers = rows[0]
        new_rows = [CRM_HEADERS]
        for row in rows[1:]:
            padded = row + [''] * max(0, len(old_headers) - len(row))
            row_dict = dict(zip(old_headers, padded))
            new_rows.append([str(row_dict.get(header, '')) for header in CRM_HEADERS])

        worksheet.clear()
        worksheet.update(new_rows, 'A1', value_input_option='RAW')
        logger.info(f'Лист «{config.GOOGLE_SHEET_NAME}»: колонки переставлены ({len(CRM_HEADERS)} шт.).')
        return True
