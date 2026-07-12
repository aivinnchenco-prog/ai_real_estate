"""Нормализация ссылок Airbnb: домен .ru + автоматическая валюта."""

from urllib.parse import parse_qs, urlparse, urlencode, urlunparse

from config import AIRBNB_CURRENCY

SUPPORTED_CURRENCIES = frozenset({'THB', 'USD', 'EUR', 'GBP'})


def resolve_currency(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    url_currency = (query.get('currency') or [''])[0].strip().upper()
    if url_currency in SUPPORTED_CURRENCIES:
        return url_currency
    return (AIRBNB_CURRENCY or 'THB').upper()


def normalize_airbnb_url(url: str) -> str:
    """
    Подготавливает ссылку для парсинга:
    - airbnb.com → airbnb.ru
    - если нет валюты (или пустая) → добавляет currency из ссылки или AIRBNB_CURRENCY
    """
    url = (url or '').strip()
    url = url.replace('.com/', '.ru/')

    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)

    existing = (query.get('currency') or [''])[0].strip().upper()
    if existing not in SUPPORTED_CURRENCIES:
        query['currency'] = [resolve_currency(url)]
    else:
        query['currency'] = [existing]

    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
