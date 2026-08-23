"""Сценарий «Взять в работу»: парсинг → фото → Агент 2 (Notion CRM).

Основной путь — передача Агенту 2 (agent2_handoff): сессия с описанием,
фото, координатами, владельцем и ценами по месяцам, дальше структуризация
в Notion + R2. Google Sheets/Drive — легаси-путь (AGENT2_HANDOFF_ENABLED=0).
"""

import time
from dataclasses import dataclass, field
from datetime import date

from agent2_handoff import find_agent2_root, handoff_to_agent2
from drive import GoogleDriveUploader
from enrich import ListingEnricher
from media import cleanup_paths, download_images

from airbnb_parser import AirbnbParser
from monthly_pricing import (
    entry_has_price,
    round_to_hundreds,
)
from owner_detect import detect_owner
from parser_pool import get_parser
from pricing_orchestrator import collect_primary_month, register_after_handoff
from sheets import GoogleSheetsWriter
from CustomLogger import logger
import config


def _seed_from_listing_price(listing_data: dict) -> dict:
    """Если при первом открытии листинга цена уже есть — кладём в месяц заезда."""
    raw = listing_data.get('Цена') or ''
    try:
        value = float(str(raw).replace(' ', '').replace('\xa0', '').replace(',', ''))
    except (TypeError, ValueError):
        return {}
    if value <= 0:
        return {}
    check_in = (listing_data.get('Дата_заезд') or '').strip()
    check_out = (listing_data.get('Дата_выезд') or '').strip()
    if not check_in:
        return {}
    try:
        start = date.fromisoformat(check_in[:10])
    except ValueError:
        return {}
    key = f'{start.year:04d}-{start.month:02d}'
    period = f'{check_in}/{check_out}' if check_out else check_in
    logger.info(f'Сидим monthly_prices из листинга: {key}={round_to_hundreds(value)}')
    return {
        key: {
            'price': round_to_hundreds(value),
            'status': 'monthly',
            'period_used': period,
            'source': 'listing_parse',
        }
    }


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


def _agent2_path(result: TakeWorkResult, parser, url: str, image_urls: list, timings: dict) -> TakeWorkResult:
    """Новый путь: primary month (blocking) + background queue для остальных месяцев.

    Owner detect и фото идут параллельно со сбором primary month.
    Agent 3 получает цену после первого месяца; months 2–12 — в фоне.
    """
    import threading

    listing_data = result.listing_data

    monthly_prices: dict = {}
    prices_thread = None
    _prices_box: dict = {}

    if not getattr(config, 'PRICE_COLLECT_ENABLED', True):
        monthly_prices = _seed_from_listing_price(listing_data)
        timings['calendar_sec'] = 0
        timings['prices_sec'] = 0
        timings['prices_deferred'] = True
        logger.info(
            'Цены по месяцам ПРОПУЩЕНЫ (PRICE_COLLECT_ENABLED=0); '
            f'сид в сессии: {len(monthly_prices)} мес.'
        )
    else:
        headless = getattr(parser, 'HEADLESS_MODE', True)

        def _prices_bg():
            p = AirbnbParser(headless=headless)
            try:
                t0 = time.perf_counter()
                prices, availability = collect_primary_month(
                    p, url, listing_data, timings
                )
                timings['prices_sec'] = round(time.perf_counter() - t0, 1)
                _prices_box['prices'] = prices
                _prices_box['availability'] = availability
            except Exception as exc:
                logger.error(f'monthly pricing error: {exc}')
            finally:
                try:
                    p.close()
                except Exception:
                    pass

        prices_thread = threading.Thread(target=_prices_bg, daemon=True, name='prices-primary')
        prices_thread.start()

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

    t0 = time.perf_counter()
    image_paths = download_images(image_urls)
    timings['photos_found'] = len(image_urls)
    timings['photos_downloaded'] = len(image_paths)
    timings['photos_download_sec'] = round(time.perf_counter() - t0, 1)
    result.image_paths = image_paths

    if prices_thread is not None:
        t0 = time.perf_counter()
        prices_thread.join(timeout=300)
        timings['prices_wait_sec'] = round(time.perf_counter() - t0, 1)
        monthly_prices = _prices_box.get('prices') or {}
        if not monthly_prices:
            monthly_prices = _seed_from_listing_price(listing_data)

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

    if prices_thread is not None and ho.object_id:
        try:
            n = register_after_handoff(
                object_id=ho.object_id,
                session_id=ho.session_id,
                listing_url=url,
                monthly_prices=monthly_prices,
                calendar=_prices_box.get('availability'),
            )
            timings['pricing_background_jobs'] = n
        except Exception as exc:
            logger.error(f'background pricing enqueue failed: {exc}')

    # Если coords не пришли с первого парса — фоновый retry доберёт точку Airbnb.
    loc = (listing_data.get('Локация') or {})
    if ho.object_id and (loc.get('latitude') is None or loc.get('longitude') is None):
        try:
            from location_retry import enqueue_location_retry
            import json as _json

            page_id = ''
            agent2_root = find_agent2_root()
            if agent2_root and ho.session_id:
                sj = agent2_root / 'data' / 'sessions' / ho.session_id / 'session.json'
                if sj.exists():
                    page_id = (_json.loads(sj.read_text(encoding='utf-8')).get('notion_page_id') or '')
            enqueue_location_retry(
                object_id=ho.object_id,
                listing_url=url,
                session_id=ho.session_id or '',
                notion_page_id=page_id,
            )
            timings['location_retry_enqueued'] = True
            logger.warning(
                f'location missing for {ho.object_id} — enqueued background retry'
            )
        except Exception as exc:
            logger.error(f'location retry enqueue failed: {exc}')

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
