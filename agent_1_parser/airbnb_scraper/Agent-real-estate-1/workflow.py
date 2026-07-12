"""Сценарий «Взять в работу»: парсинг → фото → Агент 2 (Notion CRM).

Основной путь — передача Агенту 2 (agent2_handoff): сессия с описанием,
фото, координатами, владельцем и ценами по месяцам, дальше структуризация
в Notion + R2. Google Sheets/Drive — легаси-путь (AGENT2_HANDOFF_ENABLED=0).
"""

import time
from dataclasses import dataclass, field

from agent2_handoff import find_agent2_root, handoff_to_agent2, price_months_ahead
from availability import fetch_calendar_days
from drive import GoogleDriveUploader
from enrich import ListingEnricher
from media import cleanup_paths, download_images
from monthly_pricing import collect_monthly_prices
from owner_detect import detect_owner
from parser_pool import get_parser
from sheets import GoogleSheetsWriter
from CustomLogger import logger
import config


@dataclass
class TakeWorkResult:
    object_id: str = ''
    message_text: str = ''
    image_paths: list = field(default_factory=list)
    drive_folder_url: str = ''
    uploaded_count: int = 0
    drive_note: str = ''
    listing_data: dict = field(default_factory=dict)
    enrich_texts: dict = field(default_factory=dict)
    sheet_written: bool = False
    timings: dict = field(default_factory=dict)
    # Agent 2 (Notion) путь
    session_id: str = ''
    monthly_prices: dict = field(default_factory=dict)
    owner: dict = field(default_factory=dict)


def _collect_prices(parser, url: str, timings: dict) -> dict:
    """Цены по месяцам: календарь доступности + запросы цен (см. monthly_pricing)."""
    months = price_months_ahead(find_agent2_root())
    if months <= 0:
        return {}
    t0 = time.perf_counter()
    availability = fetch_calendar_days(url)
    timings['calendar_sec'] = round(time.perf_counter() - t0, 1)

    t0 = time.perf_counter()
    prices = collect_monthly_prices(
        lambda check_in, check_out: parser.fetch_price_for_period(url, check_in, check_out),
        availability,
        months_ahead=months,
        min_segment_days=config.PRICE_MIN_SEGMENT_DAYS,
    )
    timings['prices_sec'] = round(time.perf_counter() - t0, 1)
    ok = sum(1 for v in prices.values() if v.get('price'))
    logger.info(f'Цены по месяцам: {ok}/{len(prices)} месяцев с ценой')
    return prices


def _agent2_path(result: TakeWorkResult, parser, url: str, image_urls: list, timings: dict) -> TakeWorkResult:
    """Новый путь: цены по месяцам + владелец + сессия для Агента 2."""
    listing_data = result.listing_data

    owner = {}
    if config.OWNER_DETECT_ENABLED:
        t0 = time.perf_counter()
        try:
            owner = detect_owner(
                listing_data.get('Описание', ''),
                listing_data.get('Хозяин') or {},
                web_search=config.OWNER_WEB_SEARCH,
            )
        except Exception as exc:
            logger.error(f'owner_detect error: {exc}')
        timings['owner_sec'] = round(time.perf_counter() - t0, 1)

    try:
        monthly_prices = _collect_prices(parser, url, timings)
    except Exception as exc:
        logger.error(f'monthly pricing error: {exc}')
        monthly_prices = {}

    t0 = time.perf_counter()
    image_paths = download_images(image_urls)
    timings['photos_found'] = len(image_urls)
    timings['photos_downloaded'] = len(image_paths)
    timings['photos_download_sec'] = round(time.perf_counter() - t0, 1)
    result.image_paths = image_paths

    t0 = time.perf_counter()
    ho = handoff_to_agent2(
        url=url,
        message_text=result.message_text,
        listing_data=listing_data,
        image_paths=image_paths,
        monthly_prices=monthly_prices,
        owner=owner,
    )
    timings['agent2_sec'] = round(time.perf_counter() - t0, 1)

    result.session_id = ho.session_id
    result.object_id = ho.object_id
    result.monthly_prices = monthly_prices
    result.owner = owner
    result.sheet_written = ho.agent2_ok
    result.drive_note = ho.note
    return result


def take_listing_to_work(
    url: str,
    manager: str = '',
    sheets: GoogleSheetsWriter | None = None,
    drive: GoogleDriveUploader | None = None,
    enricher: ListingEnricher | None = None,
    headless: bool = True,
) -> TakeWorkResult:
    sheets = sheets or GoogleSheetsWriter()
    drive = drive or GoogleDriveUploader()
    enricher = enricher or ListingEnricher()
    result = TakeWorkResult()
    timings = {}
    total_start = time.perf_counter()

    t0 = time.perf_counter()
    parser = get_parser(headless=headless)
    message_text, image_urls, listing_data = parser.process_url(url)
    timings['parse_sec'] = round(time.perf_counter() - t0, 1)

    result.message_text = message_text or ''
    result.listing_data = listing_data or {}

    if not result.message_text:
        result.timings = timings
        return result

    if config.AGENT2_HANDOFF_ENABLED and find_agent2_root() is not None:
        result = _agent2_path(result, parser, url, image_urls, timings)
        timings['total_sec'] = round(time.perf_counter() - total_start, 1)
        result.timings = timings
        logger.info(f'Обработка URL (Agent 2): {timings}')
        return result

    object_id = ''
    if sheets.is_configured():
        t0 = time.perf_counter()
        object_id = sheets.next_object_id()
        timings['sheet_id_sec'] = round(time.perf_counter() - t0, 1)
    result.object_id = object_id

    t0 = time.perf_counter()
    photos_found = len(image_urls)
    image_paths = download_images(image_urls)
    timings['photos_found'] = photos_found
    timings['photos_downloaded'] = len(image_paths)
    timings['photos_download_sec'] = round(time.perf_counter() - t0, 1)
    result.image_paths = image_paths

    drive_folder_url = ''
    uploaded_count = 0
    drive_note = ''

    if object_id and image_paths and drive.is_configured():
        try:
            t0 = time.perf_counter()
            drive_folder_url, uploaded_count = drive.upload_photos(object_id, image_paths)
            timings['drive_upload_sec'] = round(time.perf_counter() - t0, 1)
            result.drive_folder_url = drive_folder_url
            result.uploaded_count = uploaded_count
            if uploaded_count == 0:
                drive_note = drive.setup_hint() or 'Фото на Drive не загрузились.'
            elif drive_folder_url:
                drive_note = f'📁 Drive: {uploaded_count} фото → {drive_folder_url}'
        except Exception as exc:
            logger.error(f'Ошибка загрузки на Google Drive: {exc}')
            drive_note = f'Ошибка Drive: {exc}'

    result.drive_note = drive_note

    if enricher.is_enabled():
        try:
            enrich_texts = enricher.enrich(listing_data, object_id, drive_folder_url)
        except Exception as exc:
            logger.error(f'enrich error: {exc}')
            enrich_texts = enricher.fallback_texts(listing_data, object_id, drive_folder_url)
    else:
        enrich_texts = enricher.fallback_texts(listing_data, object_id, drive_folder_url)
    result.enrich_texts = enrich_texts

    if sheets.is_configured():
        try:
            t0 = time.perf_counter()
            written_id = sheets.append_listing(
                url,
                listing_data,
                manager=manager,
                drive_folder=drive_folder_url,
                object_id=object_id,
                photo_count=uploaded_count or len(image_paths),
                extra_fields=enrich_texts,
            )
            timings['sheet_write_sec'] = round(time.perf_counter() - t0, 1)
            result.sheet_written = bool(written_id)
            if written_id:
                result.object_id = written_id
        except Exception as exc:
            logger.error(f'Ошибка записи в Google Sheet: {exc}')

    timings['enrich_mode'] = 'sync'
    timings['enrich_fb_len'] = len(enrich_texts.get('Текст для FB', ''))
    timings['enrich_tg_len'] = len(enrich_texts.get('Текст для TG', ''))

    timings['total_sec'] = round(time.perf_counter() - total_start, 1)
    result.timings = timings
    logger.info(f'Обработка URL: {timings}')
    return result


def finalize_take_work(result: TakeWorkResult):
    cleanup_paths(result.image_paths)
    result.image_paths = []
