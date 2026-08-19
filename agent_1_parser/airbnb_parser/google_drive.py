import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import config
from CustomLogger import logger

SCOPES = [
    'https://www.googleapis.com/auth/drive',
]

_DRIVE_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]


class GoogleDriveUploader:
    def __init__(self):
        self._service = None

    def is_configured(self):
        if not config.ENABLE_DRIVE_UPLOAD:
            return False
        if not config.DRIVE_ROOT_FOLDER_ID:
            return False
        return self._has_oauth_token() or os.path.exists(config.GOOGLE_CREDENTIALS_FILE)

    def uses_oauth(self):
        return self._has_oauth_token()

    def _has_oauth_token(self):
        return os.path.exists(config.DRIVE_OAUTH_TOKEN_FILE)

    def _oauth_credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(
            config.DRIVE_OAUTH_TOKEN_FILE,
            SCOPES,
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(config.DRIVE_OAUTH_TOKEN_FILE, 'w', encoding='utf-8') as token_file:
                token_file.write(creds.to_json())
        return creds

    def _service_account_credentials(self):
        return ServiceAccountCredentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_FILE,
            scopes=_DRIVE_SCOPES,
        )

    def _get_credentials(self):
        if self._has_oauth_token():
            return self._oauth_credentials()
        return self._service_account_credentials()

    def _get_service(self):
        if self._service is not None:
            return self._service

        self._service = build('drive', 'v3', credentials=self._get_credentials())
        return self._service

    def _folder_url(self, folder_id, web_view_link=None):
        if web_view_link:
            return web_view_link
        return f'https://drive.google.com/drive/folders/{folder_id}'

    def _get_or_create_folder(self, object_id):
        service = self._get_service()
        query = (
            f"name='{object_id}' and mimeType='application/vnd.google-apps.folder' "
            f"and trashed=false"
        )
        parent = config.DRIVE_ROOT_FOLDER_ID
        if parent:
            query += f" and '{parent}' in parents"

        found = (
            service.files()
            .list(
                q=query,
                fields='files(id, webViewLink)',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
            .get('files', [])
        )
        if found:
            folder = found[0]
            return folder['id'], self._folder_url(folder['id'], folder.get('webViewLink'))

        metadata = {
            'name': object_id,
            'mimeType': 'application/vnd.google-apps.folder',
        }
        if parent:
            metadata['parents'] = [parent]

        folder = (
            service.files()
            .create(body=metadata, fields='id, webViewLink', supportsAllDrives=True)
            .execute()
        )
        return folder['id'], self._folder_url(folder['id'], folder.get('webViewLink'))

    def upload_photos(self, object_id, file_paths):
        if not file_paths:
            return '', 0

        if not self._has_oauth_token():
            logger.error(
                'Drive: service account не может загружать файлы на обычный Gmail. '
                'Запустите: python3 setup_drive_oauth.py'
            )
            return '', 0

        service = self._get_service()
        folder_id, folder_url = self._get_or_create_folder(object_id)
        uploaded = 0
        errors = []

        def _upload_one(index, path):
            if not os.path.exists(path):
                return False, f'{path}: файл не найден'
            try:
                mime_type, _ = mimetypes.guess_type(path)
                media = MediaFileUpload(
                    path,
                    mimetype=mime_type or 'image/jpeg',
                    resumable=False,
                )
                service.files().create(
                    body={
                        'name': f'{index:02d}_{os.path.basename(path)}',
                        'parents': [folder_id],
                    },
                    media_body=media,
                    fields='id',
                    supportsAllDrives=True,
                ).execute()
                return True, ''
            except HttpError as exc:
                return False, f'фото {index}: {exc}'

        workers = max(1, min(config.DRIVE_UPLOAD_WORKERS, len(file_paths)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_upload_one, index, path): index
                for index, path in enumerate(file_paths, start=1)
            }
            for future in as_completed(futures):
                ok, err = future.result()
                if ok:
                    uploaded += 1
                elif err:
                    errors.append(err)
                    logger.error(f'Drive upload error: {err}')

        if uploaded:
            logger.info(f'На Drive загружено {uploaded} фото в папку {object_id}.')
        elif errors:
            logger.error(f'Drive: ни одно фото не загружено в {object_id}: {"; ".join(errors[:3])}')

        return folder_url, uploaded

    def setup_hint(self) -> str:
        if self._has_oauth_token():
            return ''
        return (
            'Фото на Drive не загружаются без OAuth.\n'
            'Один раз на Mac выполните:\n'
            'python3 setup_drive_oauth.py'
        )
