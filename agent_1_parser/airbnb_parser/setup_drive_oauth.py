#!/usr/bin/env python3
"""Одноразовая авторизация Google Drive через ваш Gmail (не service account)."""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

import config

SCOPES = ['https://www.googleapis.com/auth/drive']


def main():
    client_file = config.DRIVE_OAUTH_CLIENT_FILE
    token_file = config.DRIVE_OAUTH_TOKEN_FILE

    if not os.path.exists(client_file):
        print('Не найден OAuth-клиент:', client_file)
        print()
        print('Сделайте так:')
        print('1. Google Cloud Console → APIs & Services → Credentials')
        print('2. Create Credentials → OAuth client ID → Desktop app')
        print('3. Скачайте JSON и сохраните как:')
        print(f'   {client_file}')
        sys.exit(1)

    os.makedirs(os.path.dirname(token_file), exist_ok=True)

    flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(token_file, 'w', encoding='utf-8') as f:
        f.write(creds.to_json())

    print('Готово! Токен сохранён:', token_file)
    print('Теперь бот сможет загружать фото в вашу папку на Drive.')


if __name__ == '__main__':
    main()
