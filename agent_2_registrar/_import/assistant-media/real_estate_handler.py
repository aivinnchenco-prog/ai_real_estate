#!/usr/bin/env python3
"""
Real Estate CRM Handler
Работает с Notion и Cloudflare R2
"""

import hashlib
import hmac
import mimetypes
import os
import json
import re
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from urllib.parse import quote

# Config
NOTION_API_KEY = os.getenv('NOTION_API_KEY')
NOTION_DB_ID = os.getenv('NOTION_DB_ID') or os.getenv('NOTION_DATABASE_ID')
CLOUDFLARE_ENDPOINT = os.getenv('CLOUDFLARE_ENDPOINT')
CLOUDFLARE_BUCKET = os.getenv('CLOUDFLARE_BUCKET')
CLOUDFLARE_ACCESS_KEY = os.getenv('CLOUDFLARE_ACCESS_KEY_ID')
CLOUDFLARE_SECRET = os.getenv('CLOUDFLARE_SECRET_ACCESS_KEY')
CLOUDFLARE_PUBLIC_URL = os.getenv('CLOUDFLARE_PUBLIC_BASE_URL')
CONTACT_PHONE = os.getenv('CONTACT_PHONE', '+66625124002')

NOTION_API_VERSION = '2022-06-28'
NOTION_API_URL = 'https://api.notion.com/v1'


class NotionCRM:
    """Работа с базой Notion"""
    
    def __init__(self, api_key: str, db_id: str):
        self.api_key = api_key
        self.db_id = db_id
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Notion-Version': NOTION_API_VERSION,
            'Content-Type': 'application/json'
        }
    
    def get_all_properties(self) -> Dict:
        """Получить схему базы"""
        url = f'{NOTION_API_URL}/databases/{self.db_id}'
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json().get('properties', {})
    
    def query_by_status(self, status: str, limit: int = 10) -> List[Dict]:
        """Получить объекты с определённым статусом"""
        url = f'{NOTION_API_URL}/databases/{self.db_id}/query'
        payload = {
            'filter': {
                'property': 'Статус',
                'status': {
                    'equals': status
                }
            },
            'page_size': limit
        }
        resp = requests.post(url, headers=self.headers, json=payload)
        resp.raise_for_status()
        return resp.json().get('results', [])
    
    def query_by_object_id(self, object_id: str) -> Optional[Dict]:
        """Найти объект по ID"""
        url = f'{NOTION_API_URL}/databases/{self.db_id}/query'
        payload = {
            'filter': {
                'property': 'Объект ID',
                'rich_text': {
                    'equals': object_id
                }
            }
        }
        resp = requests.post(url, headers=self.headers, json=payload)
        resp.raise_for_status()
        results = resp.json().get('results', [])
        return results[0] if results else None

    def list_object_ids_for_date(self, date_prefix: str) -> List[str]:
        """Все Объект ID за день: F_YYYYMMDD_001, A_YYYYMMDD_002, … (и легаси без префикса)."""
        url = f'{NOTION_API_URL}/databases/{self.db_id}/query'
        # contains, а не starts_with: ID теперь начинаются с префикса источника (F_/A_).
        needle = f'{date_prefix}_'
        ids: List[str] = []
        start_cursor = None
        while True:
            payload: Dict[str, Any] = {
                'filter': {
                    'property': 'Объект ID',
                    'rich_text': {'contains': needle},
                },
                'page_size': 100,
            }
            if start_cursor:
                payload['start_cursor'] = start_cursor
            resp = requests.post(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            for page in data.get('results', []):
                prop = page.get('properties', {}).get('Объект ID', {})
                items = prop.get('rich_text') or []
                text = ''.join(t.get('plain_text', '') for t in items).strip()
                if text:
                    ids.append(text)
            if not data.get('has_more'):
                break
            start_cursor = data.get('next_cursor')
        return ids
    
    def create_page(self, properties: Dict) -> Dict:
        """Создать новую страницу в базе"""
        url = f'{NOTION_API_URL}/pages'
        payload = {
            'parent': {'database_id': self.db_id},
            'properties': properties
        }
        resp = requests.post(url, headers=self.headers, json=payload)
        resp.raise_for_status()
        return resp.json()
    
    def update_page(self, page_id: str, properties: Dict) -> Dict:
        """Обновить страницу"""
        url = f'{NOTION_API_URL}/pages/{page_id}'
        payload = {'properties': properties}
        resp = requests.patch(url, headers=self.headers, json=payload)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def read_number(page: Dict, field: str) -> int:
        prop = page.get('properties', {}).get(field, {})
        val = prop.get('number')
        return int(val) if val is not None else 0

    def set_error(self, page_id: str, page: Dict, message: str, *, status: str | None = None) -> None:
        """Write last_error + increment error_count; optional status."""
        props: Dict = {
            'last_error': self.build_text(message[:4000]),
            'error_count': self.build_number(self.read_number(page, 'error_count') + 1),
        }
        if status:
            props['Статус'] = self.build_status(status)
        self.update_page(page_id, props)
    
    @staticmethod
    def build_title(text: str) -> Dict:
        return {
            'title': [{'type': 'text', 'text': {'content': text}}]
        }
    
    @staticmethod
    def build_text(text: str, max_chunk: int = 2000) -> Dict:
        content = text or ""
        chunks = [content[i : i + max_chunk] for i in range(0, max(len(content), 1), max_chunk)]
        return {
            'rich_text': [{'type': 'text', 'text': {'content': chunk}} for chunk in chunks]
        }
    
    @staticmethod
    def build_url(url: str) -> Dict:
        return {'url': url}
    
    @staticmethod
    def build_number(value: float) -> Dict:
        return {'number': value}
    
    @staticmethod
    def build_checkbox(checked: bool) -> Dict:
        return {'checkbox': checked}
    
    @staticmethod
    def build_date(date_str: str) -> Dict:
        """date_str: 'YYYY-MM-DD'"""
        return {'date': {'start': date_str}}
    
    @staticmethod
    def build_select(name: str) -> Dict:
        return {'select': {'name': name}}
    
    @staticmethod
    def build_multi_select(names: List[str]) -> Dict:
        return {
            'multi_select': [{'name': name} for name in names]
        }
    
    @staticmethod
    def build_status(status: str) -> Dict:
        return {'status': {'name': status}}


class CloudflareR2:
    """Работа с Cloudflare R2 (S3-compatible, AWS SigV4)"""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        public_url: str,
        account_id: Optional[str] = None,
    ):
        self.endpoint = endpoint.rstrip('/')
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.public_url = public_url.rstrip('/')
        self.account_id = account_id or self._account_id_from_endpoint(endpoint)

    @staticmethod
    def _account_id_from_endpoint(endpoint: str) -> str:
        host = endpoint.replace('https://', '').replace('http://', '').split('/')[0]
        return host.split('.')[0]

    def _signed_put(self, key: str, data: bytes, content_type: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc)
        amz_date = now.strftime('%Y%m%dT%H%M%SZ')
        date_stamp = now.strftime('%Y%m%d')
        payload_hash = hashlib.sha256(data).hexdigest()

        canonical_uri = f'/{self.bucket}/{key}'
        host = f'{self.account_id}.r2.cloudflarestorage.com'
        canonical_headers = (
            f'host:{host}\n'
            f'x-amz-content-sha256:{payload_hash}\n'
            f'x-amz-date:{amz_date}\n'
        )
        signed_headers = 'host;x-amz-content-sha256;x-amz-date'
        canonical_request = (
            f'PUT\n{canonical_uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}'
        )

        credential_scope = f'{date_stamp}/auto/s3/aws4_request'
        string_to_sign = (
            'AWS4-HMAC-SHA256\n'
            f'{amz_date}\n{credential_scope}\n'
            f'{hashlib.sha256(canonical_request.encode()).hexdigest()}'
        )

        k_date = hmac.new(f'AWS4{self.secret_key}'.encode(), date_stamp.encode(), hashlib.sha256).digest()
        k_region = hmac.new(k_date, b'auto', hashlib.sha256).digest()
        k_service = hmac.new(k_region, b's3', hashlib.sha256).digest()
        k_signing = hmac.new(k_service, b'aws4_request', hashlib.sha256).digest()
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth = (
            f'AWS4-HMAC-SHA256 Credential={self.access_key}/{credential_scope}, '
            f'SignedHeaders={signed_headers}, Signature={signature}'
        )

        headers = {
            'Authorization': auth,
            'x-amz-content-sha256': payload_hash,
            'x-amz-date': amz_date,
            'Content-Length': str(len(data)),
        }
        if content_type:
            headers['Content-Type'] = content_type

        upload_url = f'{self.endpoint}/{self.bucket}/{key}'
        upload_resp = requests.put(upload_url, data=data, headers=headers, timeout=120)
        upload_resp.raise_for_status()

    def upload_from_url(self, source_url: str, dest_path: str) -> str:
        """
        Скачать файл по URL и загрузить в R2
        dest_path: 'object_id/photos/main.jpg'
        Возвращает публичный URL
        """
        resp = requests.get(source_url, stream=True, timeout=120)
        resp.raise_for_status()
        content_type = resp.headers.get('Content-Type') or mimetypes.guess_type(dest_path)[0]
        self._signed_put(dest_path, resp.content, content_type)
        return f'{self.public_url}/{quote(dest_path)}'

    def upload_from_path(self, local_path: str, dest_path: str) -> str:
        """Загрузить локальный файл"""
        with open(local_path, 'rb') as f:
            data = f.read()
        content_type = mimetypes.guess_type(local_path)[0] or 'application/octet-stream'
        self._signed_put(dest_path, data, content_type)
        return f'{self.public_url}/{quote(dest_path)}'

    def upload_bytes(self, data: bytes, dest_path: str, content_type: str = 'application/octet-stream') -> str:
        """Загрузить bytes (например index.html галереи)."""
        self._signed_put(dest_path, data, content_type)
        return f'{self.public_url}/{quote(dest_path)}'
    
    def get_public_url(self, dest_path: str) -> str:
        """Получить публичный URL без загрузки"""
        return f'{self.public_url}/{quote(dest_path)}'


class ObjectIDGenerator:
    """Генератор уникальных ID: F_YYYYMMDD_NNN (Facebook) / A_YYYYMMDD_NNN (Airbnb)."""

    # Легаси-ID без префикса источника (YYYYMMDD_NNN) тоже учитываются в нумерации дня.
    ID_PATTERN = re.compile(r'^(?:([FA])_)?(\d{8})_(\d{3,})$')

    @staticmethod
    def date_prefix(when: Optional[datetime] = None) -> str:
        dt = when or datetime.now(timezone.utc)
        return dt.strftime('%Y%m%d')

    @staticmethod
    def source_prefix(source: Optional[str]) -> str:
        s = (source or '').strip().upper()
        if s.startswith('A'):
            return 'A'
        return 'F'

    @staticmethod
    def next_sequence(crm: NotionCRM, date_prefix: str) -> int:
        max_seq = 0
        for oid in crm.list_object_ids_for_date(date_prefix):
            m = ObjectIDGenerator.ID_PATTERN.match(oid)
            if m and m.group(2) == date_prefix:
                max_seq = max(max_seq, int(m.group(3)))
        return max_seq + 1

    @staticmethod
    def generate(crm: NotionCRM, when: Optional[datetime] = None, source: str = 'F') -> str:
        """
        Формат: F_YYYYMMDD_NNN / A_YYYYMMDD_NNN
        - F/A — источник объявления (Facebook / Airbnb)
        - YYYY MM DD — дата добавления (UTC)
        - NNN — порядковый номер объекта за этот день (001, 002, …), сквозной по источникам
        """
        src = ObjectIDGenerator.source_prefix(source)
        prefix = ObjectIDGenerator.date_prefix(when)
        seq = ObjectIDGenerator.next_sequence(crm, prefix)
        return f'{src}_{prefix}_{seq:03d}'


class DescriptionGenerator:
    """Генератор описаний для разных каналов"""
    
    @staticmethod
    def telegram(
        title: str,
        description: str,
        price_monthly: Optional[float] = None,
        price_yearly: Optional[float] = None,
        rooms: Optional[int] = None,
        area: Optional[float] = None,
        amenities: Optional[List[str]] = None,
        object_id: str = '',
        contact: str = CONTACT_PHONE,
        available_from: Optional[str] = None
    ) -> str:
        """Генерирует описание для Telegram"""
        lines = [
            f'🏠 {title}',
            ''
        ]
        
        if rooms:
            lines.append(f'🛏️ {rooms} комнат(ы)')
        if area:
            lines.append(f'📐 {area:.0f} м²')
        if amenities:
            amenities_str = ', '.join(amenities[:5])  # Первые 5
            lines.append(f'✨ {amenities_str}')
        
        if price_monthly:
            lines.append(f'💰 {price_monthly:,.0f} ฿/месяц')
        if price_yearly and not price_monthly:
            yearly_monthly = price_yearly / 12
            lines.append(f'💰 {yearly_monthly:,.0f} ฿/месяц ({price_yearly:,.0f} ฿/год)')
        
        if available_from:
            lines.append(f'📅 Доступно: {available_from}')
        
        lines.extend(['', description, ''])
        
        if contact:
            lines.append(f'☎️ Связь: {contact}')
        if object_id:
            lines.append(f'🔍 #{object_id}')
        
        return '\n'.join(lines)
    
    @staticmethod
    def facebook(
        title: str,
        description: str,
        price_monthly: Optional[float] = None,
        rooms: Optional[int] = None,
        area: Optional[float] = None,
        type_housing: Optional[str] = None,
        amenities: Optional[List[str]] = None,
        object_id: str = '',
        contact: str = CONTACT_PHONE
    ) -> str:
        """Генерирует описание для Facebook Marketplace"""
        lines = [
            f'{title}',
            ''
        ]
        
        if type_housing:
            lines.append(f'🏠 Тип: {type_housing}')
        if area:
            lines.append(f'📐 Площадь: {area:.0f} м²')
        if rooms:
            lines.append(f'🛏️ Комнат: {rooms}')
        if price_monthly:
            lines.append(f'💰 Цена: {price_monthly:,.0f} ฿/месяц')
        
        lines.extend(['', description, ''])
        
        if amenities:
            amenities_str = ' #'.join(amenities[:8])
            lines.append(f'#{amenities_str}')
        
        if contact:
            lines.append(f'Контакт: {contact}')
        if object_id:
            lines.append(f'#{object_id}')
        
        return '\n'.join(lines)


def run_schema_check(skip: bool) -> None:
    """Валидация живой схемы Notion против schema/notion_schema.json (общий контракт репо)."""
    import subprocess
    import sys
    from pathlib import Path

    if skip or os.environ.get('SKIP_SCHEMA_CHECK') == '1':
        print('[schema] проверка схемы пропущена (--skip-schema-check)')
        return
    for parent in Path(__file__).resolve().parents:
        validator = parent / 'schema' / 'validate_schema.py'
        if validator.exists():
            proc = subprocess.run([sys.executable, str(validator)])
            if proc.returncode != 0:
                print(
                    '[schema] Схема Notion не совпадает с контрактом. '
                    'Исправь таблицу/контракт или запусти с --skip-schema-check.',
                    file=sys.stderr,
                )
                sys.exit(2)
            return
    print('[schema] validate_schema.py не найден — проверка схемы пропущена', file=sys.stderr)


# Main exports
if __name__ == '__main__':
    import sys

    run_schema_check('--skip-schema-check' in sys.argv)

    if len(sys.argv) > 1 and sys.argv[1] == '--next-id':
        if not NOTION_API_KEY or not NOTION_DB_ID:
            print('ERROR: set NOTION_API_KEY and NOTION_DB_ID', file=sys.stderr)
            sys.exit(1)
        crm = NotionCRM(NOTION_API_KEY, NOTION_DB_ID)
        print(ObjectIDGenerator.generate(crm))
        sys.exit(0)

    print('✅ Real Estate Handler module ready')
    print(f'  Notion DB: {(NOTION_DB_ID or "")[:8]}...')
    print(f'  Cloudflare Bucket: {CLOUDFLARE_BUCKET}')
    print(f'  Contact: {CONTACT_PHONE}')
    print(f'  Object ID format: F_YYYYMMDD_NNN / A_YYYYMMDD_NNN (example: F_{ObjectIDGenerator.date_prefix()}_001)')
