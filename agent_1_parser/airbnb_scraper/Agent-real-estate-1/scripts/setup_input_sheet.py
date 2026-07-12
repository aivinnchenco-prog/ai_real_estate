"""
Создаёт лист «Вход» с упрощёнными заголовками в существующей таблице.
Запуск: python scripts/setup_input_sheet.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gspread
from google.oauth2.service_account import Credentials

import config
from google_sheets import INPUT_HEADERS, SCOPES


def main():
    if not config.GOOGLE_SPREADSHEET_ID:
        print('Задайте GOOGLE_SPREADSHEET_ID в config.py или через env.')
        sys.exit(1)

    creds_path = Path(config.GOOGLE_CREDENTIALS_FILE)
    if not creds_path.exists():
        print(f'Не найден ключ: {creds_path}')
        sys.exit(1)

    credentials = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(config.GOOGLE_SPREADSHEET_ID)

    sheet_name = config.GOOGLE_SHEET_NAME
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
        print(f'Лист «{sheet_name}» уже существует.')
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=sheet_name,
            rows=1000,
            cols=len(INPUT_HEADERS),
        )
        print(f'Лист «{sheet_name}» создан.')

    first_row = worksheet.row_values(1)
    if not first_row:
        worksheet.append_row(INPUT_HEADERS, value_input_option='RAW')
        print('Заголовки добавлены.')
    elif first_row[:len(INPUT_HEADERS)] == INPUT_HEADERS:
        print('Заголовки уже на месте.')
    else:
        print('Внимание: первая строка не совпадает с ожидаемыми заголовками.')
        print('Ожидалось:', INPUT_HEADERS)
        print('Сейчас:   ', first_row)

    print(f'\nТаблица: https://docs.google.com/spreadsheets/d/{config.GOOGLE_SPREADSHEET_ID}/edit')
    print(f'Лист для бота: «{sheet_name}»')
    print('Лист «CRM_Объекты» не трогаем — для ручной CRM.')


if __name__ == '__main__':
    main()
