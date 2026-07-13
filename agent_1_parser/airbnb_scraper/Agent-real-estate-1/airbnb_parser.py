from seleniumbase import sb_cdp
from seleniumbase import SB
import re
from lxml import html
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import json
from urllib.parse import parse_qs, urlparse, urlencode, urlunparse

from airbnb_url import normalize_airbnb_url, resolve_currency
from ai_extract import AiExtractor
import config
from CustomLogger import logger
from datetime import date, datetime
import traceback
import random
import sys
import os


class AirbnbParser:

    VERSION = '2.0'

    TARGET_HIGHLIGHTS = [
        'Удобства для повседневной жизни',
        'Развлечения на свежем воздухе',
    ]

    AMENITIES_TO_SKIP_TITLE = [
        'Спальня и прачечная',
        'Развлечения',
        'Отопление и охлаждение',
        'Безопасность жилья',
        'Кухня и столовая',
        'Парковка и объекты',
    ]

    AMENITIES_TO_SAVE_TITLE = [
        'Интернет и рабочее место',
    ]

    CURRENCY_PRIORITY = {'THB': 0, 'USD': 1, 'RUB': 9}
    _PRICE_FIELDS = ('Цена', 'Цена_отображение', 'Цена_строка', 'Цена_источник')

    @property
    def WAIT_TIMEOUT(self):
        return config.PARSER_WAIT_TIMEOUT

    def __init__(self, headless=True):
        self.HEADLESS_MODE = headless
        self._dom_price_cache = None
        self.sb = None

    def _ensure_sb(self):
        if self.sb is None:
            self._init_sb()
        return self.sb

    def _init_sb(self):
        logger.info('Initializing Selenium Base...')
        self.sb = sb_cdp.Chrome(
            url="about:blank",
            lang="en",
            # headless=False,
            headless=self.HEADLESS_MODE,
        )
        self.sb.maximize()
        return self.sb

    def process_url(self, url):
        logger.info(f'Processing URL: {url}')
        self._target_currency = resolve_currency(url)
        url = normalize_airbnb_url(url)
        self._target_currency = resolve_currency(url)
        logger.info(f'Open with currency {self._target_currency}: {url}')
        url_dates = self._parse_url_dates(url)
        url_dates['currency'] = self._target_currency
        page_source = ''
        sb = self._ensure_sb()
        try:
            self._open_listing_url(url)
            sb.wait_for_element_visible(
                '//button[@data-testid="user-flag-report-button"]', timeout=self.WAIT_TIMEOUT)
            self._close_popup()
            if config.PARSER_PAGE_SLEEP_SEC > 0:
                time.sleep(config.PARSER_PAGE_SLEEP_SEC)
            page_source = self._wait_for_price_page_source()
            page_source = self._wait_for_photos_page_source(page_source)
        except Exception:
            with open('Error.html', 'w', encoding='utf-8') as f:
                f.write(sb.get_page_source())
            page_source = sb.get_page_source()

        data = self._parse(page_source, url_dates)

        # Airbnb иногда отдаёт SSR-JSON без автоперевода (описание на языке
        # хозяина, например китайском). Свежая перезагрузка обычно приносит
        # переведённую версию — пробуем один раз.
        if self._cjk_ratio(data.get('Описание', '')) > 0.15:
            logger.info('Описание не переведено (CJK) — перезагружаю страницу для автоперевода')
            try:
                self._open_listing_url(url)
                sb.wait_for_element_visible(
                    '//button[@data-testid="user-flag-report-button"]', timeout=self.WAIT_TIMEOUT)
                self._close_popup()
                if config.PARSER_PAGE_SLEEP_SEC > 0:
                    time.sleep(config.PARSER_PAGE_SLEEP_SEC)
                retry_source = self._wait_for_photos_page_source(self._wait_for_price_page_source())
                retry_data = self._parse(retry_source, url_dates)
                if self._cjk_ratio(retry_data.get('Описание', '')) <= 0.15:
                    data, page_source = retry_data, retry_source
                    logger.info('Автоперевод получен со второй загрузки')
                else:
                    logger.warning('Описание осталось без перевода — переведёт Агент 2')
            except Exception:
                logger.warning('Ретрай автоперевода не удался', exc_info=True)

        self._clear_price_fields(data)
        price_fields = self._extract_final_price(page_source, data, url_dates, url)
        if price_fields:
            data.update(price_fields)
            logger.info(
                f'Price ({self._target_currency}): {data.get("Цена_отображение", "")} '
                f'[{data.get("Цена_источник", "parser")}]'
            )
        else:
            logger.warning(
                f'Price not extracted in {self._target_currency} — '
                'RUB or wrong currency ignored, sheet price will be empty'
            )
        message_text = self._generate_message_text(data)
        media = data['Изображения']
        logger.info(f'Найдено URL фото на листинге: {len(media)}')

        if not config.PARSER_REUSE_BROWSER and self.sb is not None:
            self.sb.close_active_tab()
            self.sb.driver.quit()
            self.sb = None

        if config.MAX_PHOTOS_DOWNLOAD > 0 and len(media) > config.MAX_PHOTOS_DOWNLOAD:
            media = media[:config.MAX_PHOTOS_DOWNLOAD]

        return message_text, media, data

    def fetch_price_for_period(self, url, check_in, check_out):
        """Цена за конкретный период (check_in/check_out, ISO-даты).

        Возвращает float (в валюте _target_currency, обычно THB) или None,
        если Airbnb не отдал цену (даты заняты/недоступны).
        Используется модулем monthly_pricing для цен по месяцам.
        """
        self._target_currency = resolve_currency(url)
        base = normalize_airbnb_url(url)
        parts = urlparse(base)
        query = parse_qs(parts.query)
        query['check_in'] = [str(check_in)]
        query['check_out'] = [str(check_out)]
        dated_url = urlunparse(parts._replace(query=urlencode(query, doseq=True)))

        try:
            self._open_listing_url(dated_url)
            if config.PARSER_PAGE_SLEEP_SEC > 0:
                time.sleep(config.PARSER_PAGE_SLEEP_SEC)
            page_source = self._wait_for_price_page_source()
        except Exception:
            logger.warning(f'fetch_price_for_period: не удалось открыть {check_in}/{check_out}', exc_info=True)
            return None

        # Та же цепочка экстракторов, что и в process_url: HTML (aria-label)
        # надёжнее всего — раньше его тут не было, и цены «не находились».
        amount, display = self._extract_price_from_html(page_source)
        if not amount:
            amount, display = self._find_price_in_json_text(page_source)
        if not amount:
            parsed = self._extract_price_from_dom()
            amount = parsed.get('Цена', '')
            display = parsed.get('Цена_отображение', display)
        if not amount:
            logger.info(f'fetch_price_for_period {check_in}/{check_out}: цены нет (даты заняты?)')
            return None
        try:
            value = float(amount)
        except ValueError:
            return None

        # Airbnb для длинных стеев показывает ставку «X ฿ помесячно».
        # Для полного месяца это и есть цена месяца; для короткого отрезка
        # приводим ставку к стоимости отрезка (дальше monthly_pricing
        # экстраполирует обратно к 30 дням).
        try:
            # Включительно, как segment_days в monthly_pricing (17..30 сент = 14)
            days = (date.fromisoformat(str(check_out)) - date.fromisoformat(str(check_in))).days + 1
        except ValueError:
            days = None
        if days and days < 27 and re.search(r'помесячно|month', str(display), re.I):
            value = value * days / 30.0
        logger.info(f'fetch_price_for_period {check_in}/{check_out}: {display} -> {value:.0f}')
        return value

    def close(self):
        """Закрывает браузер; зависший driver.quit() не блокирует бота."""
        if self.sb is None:
            return
        import threading

        sb, self.sb = self.sb, None

        def _quit():
            try:
                sb.close_active_tab()
                sb.driver.quit()
            except Exception:
                pass

        worker = threading.Thread(target=_quit, daemon=True)
        worker.start()
        worker.join(timeout=15)
        if worker.is_alive():
            logger.warning('driver.quit() завис — оставляю Chrome умирать в фоне')

    @staticmethod
    def _cjk_ratio(text: str) -> float:
        """Доля CJK-символов (кит./яп./кор.) в тексте."""
        if not text:
            return 0.0
        cjk = sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff')
        return cjk / len(text)

    def _open_listing_url(self, url):
        """Открывает листинг с cookie валюты — airbnb.ru иначе показывает RUB.

        Открытие ограничено таймаутом: зависший Chrome (после долгого простоя
        или капчи) перезапускается, попытка повторяется один раз.
        """
        try:
            self._open_with_timeout(url, config.PARSER_OPEN_TIMEOUT_SEC)
        except TimeoutError:
            logger.warning(
                f'Открытие страницы зависло (> {config.PARSER_OPEN_TIMEOUT_SEC}с) — '
                'перезапускаю браузер и пробую ещё раз'
            )
            self.close()
            self._open_with_timeout(url, config.PARSER_OPEN_TIMEOUT_SEC)

    def _open_with_timeout(self, url, timeout_sec):
        import threading

        sb = self._ensure_sb()
        currency = getattr(self, '_target_currency', None) or 'THB'
        try:
            from mycdp import network as cdp_network

            sb.set_all_cookies([
                cdp_network.CookieParam(
                    name='currency',
                    value=currency,
                    domain='.airbnb.ru',
                    path='/',
                ),
            ])
        except Exception:
            logger.debug('Could not set currency cookie', exc_info=True)

        done = threading.Event()
        errors: list = []

        def _go():
            try:
                sb.open(url)
            except Exception as e:  # noqa: BLE001 — пробрасываем наружу
                errors.append(e)
            finally:
                done.set()

        worker = threading.Thread(target=_go, daemon=True)
        worker.start()
        if not done.wait(timeout=timeout_sec):
            raise TimeoutError(f'sb.open({url}) не завершился за {timeout_sec}с')
        if errors:
            raise errors[0]

    def _clear_price_fields(self, data):
        for key in self._PRICE_FIELDS:
            data.pop(key, None)

    def _validate_price_fields(self, fields, target_currency=None):
        if not fields or not fields.get('Цена'):
            return {}
        target = (target_currency or getattr(self, '_target_currency', None) or '').upper()
        display = (fields.get('Цена_отображение') or fields.get('Цена_строка') or '').split('(')[0].strip()
        if not display or not self._accept_price_display(display, target):
            return {}
        compact = display.replace(' ', '').replace('\xa0', '')
        if re.fullmatch(r'[$฿]\d{3,}', compact):
            return {}
        return fields

    def _parse_url_dates(self, url):
        query = parse_qs(urlparse(url).query)
        check_in = (query.get('check_in') or [''])[0]
        check_out = (query.get('check_out') or [''])[0]
        period = ''
        if check_in and check_out:
            period = f'{check_in} — {check_out}'
        return {
            'check_in': check_in,
            'check_out': check_out,
            'period': period,
            'guests': (query.get('guests') or [''])[0],
        }

    def _wait_for_price_page_source(self, timeout=None):
        timeout = config.PARSER_PRICE_WAIT_SEC if timeout is None else timeout
        deadline = time.time() + timeout
        sb = self._ensure_sb()
        last_source = sb.get_page_source()
        while time.time() < deadline:
            last_source = sb.get_page_source()
            if self._extract_price_from_html(last_source)[0]:
                break
            if self._find_price_in_json_text(last_source)[0]:
                break
            time.sleep(0.5)
        if not self._extract_price_from_html(last_source)[0]:
            if self._extract_price_from_dom().get('Цена'):
                last_source = sb.get_page_source()
        return last_source

    def _wait_for_photos_page_source(self, page_source):
        """Ждём, пока в JSON страницы подгрузятся все фото листинга."""
        timeout = config.PARSER_PHOTO_WAIT_SEC
        poll = config.PARSER_PHOTO_POLL_SEC
        if timeout <= 0:
            return page_source

        sb = self._ensure_sb()
        deadline = time.time() + timeout
        best_source = page_source
        best_count = self._count_listing_photos(page_source)
        stable_rounds = 0

        while time.time() < deadline:
            time.sleep(poll)
            current = sb.get_page_source()
            count = self._count_listing_photos(current)
            if count > best_count:
                best_count = count
                best_source = current
                stable_rounds = 0
            elif count == best_count and count > 0:
                stable_rounds += 1
                if stable_rounds >= 2:
                    break
            else:
                stable_rounds = 0

        logger.info(f'Фото в JSON страницы: {best_count}')
        return best_source

    def _load_deferred_state(self, page_source):
        tree = html.fromstring(page_source)
        json_blobs = tree.xpath('//script[starts-with(@id, "data-deferred-state-")]/text()')
        if not json_blobs:
            fallback = tree.xpath('string(//script[@id="data-deferred-state-0"]/text())')
            json_blobs = [fallback] if fallback else []

        data = {}
        for blob in json_blobs:
            if not blob:
                continue
            try:
                parsed = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            if self._has_listing_data(parsed):
                return parsed
            if not data:
                data = parsed
        return data

    def _count_listing_photos(self, page_source):
        data = self._load_deferred_state(page_source)
        if not data:
            return 0
        return len(self._extract_photo_urls_from_data(data))

    def _is_listing_photo_url(self, url):
        if not url or not isinstance(url, str):
            return False
        return '/pictures/hosting/' in url

    def _extract_photo_urls_from_data(self, data):
        urls = []
        seen = set()

        def add_url(url):
            if not self._is_listing_photo_url(url) or url in seen:
                return
            seen.add(url)
            urls.append(url)

        try:
            sections = (
                data['niobeClientData'][0][1]['data']['presentation']
                ['stayProductDetailPage']['sections']['sections']
            )
            for section in sections:
                if section.get('sectionId') != 'PHOTO_TOUR_SCROLLABLE_MODAL':
                    continue
                for item in (section.get('section') or {}).get('mediaItems') or []:
                    add_url(item.get('baseUrl'))
        except (KeyError, IndexError, TypeError):
            pass

        if urls:
            return urls

        def walk(obj):
            if isinstance(obj, dict):
                base_url = obj.get('baseUrl')
                if base_url:
                    add_url(base_url)
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)

        walk(data)
        return urls

    def _generate_message_text(self, data):
        price_line = data.get('Цена_строка', '')
        period_line = data.get('Период', '')
        header = ''
        if price_line:
            header += f'\n💰 {price_line}'
        if period_line:
            header += f'\n📅 {period_line}'

        message_text = f"""
{data['Название']}

{data['Название_2']}
{data['Обзор']}{header}

{data['Особенности']}

{data['Описание']}
{data['Удобства']}
"""
        return message_text

    def _close_popup(self):
        try:
            self._ensure_sb().click('//button[@aria-label="Закрыть"]', timeout=3)
        except:
            pass

    def parse_saved_json(self, json_path, url_dates=None, page_source=''):
        with open(json_path, encoding='utf-8') as json_file:
            data = json.load(json_file)
        details = self._get_details(data)
        details.update(self._extract_price_info(data, page_source, url_dates or {}))
        return details

    def _parse(self, page_source, url_dates=None):
        url_dates = url_dates or {}
        data = self._load_deferred_state(page_source)

        if config.PARSER_SAVE_DEBUG_JSON:
            samples_dir = 'Samples'
            os.makedirs(samples_dir, exist_ok=True)
            with open(os.path.join(samples_dir, 'Details.json'), 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        details = self._get_details(data)
        details.update(self._extract_price_info(data, page_source, url_dates))
        return details

    def _has_listing_data(self, data):
        try:
            data['niobeClientData'][0][1]['data']['presentation']['stayProductDetailPage']
            return True
        except (KeyError, IndexError, TypeError):
            return False

    def _get_details(self, data):
        details = {}

        for section in data['niobeClientData'][0][1]['data']['presentation']['stayProductDetailPage']['sections']['sections']:
            if section['sectionId'] == 'AVAILABILITY_CALENDAR_DEFAULT':
                details['Название'] = section['section']['listingTitle']
            if section['sectionId'] == 'DESCRIPTION_DEFAULT':
                details['Описание'] = section['section']['htmlDescription']['htmlText'].replace('<br />', '\n')
            if section['sectionId'] == 'HIGHLIGHTS_DEFAULT':
                details['Особенности'] = self._get_highlights(section)
            if section['sectionId'] == 'PHOTO_TOUR_SCROLLABLE_MODAL':
                details['Изображения'] = self._get_photos(section, data)
            if section['sectionId'] == 'LOCATION_DEFAULT':
                details['Локация'] = self._get_location(section)
            if section['sectionId'] == 'MEET_YOUR_HOST':
                details['Хозяин'] = self._get_host(section)
        
        # for section in data['niobeClientData'][0][1]['data']['node']['pdpPresentation']['amenities']['seeAllAmenitiesGroups']:
        #     if section['sectionId'] == 'AMENITIES_DEFAULT':
        details['Удобства'] = self._get_amenities(data['niobeClientData'][0][1]['data']['node']['pdpPresentation']['amenities']['seeAllAmenitiesGroups'])
        
        # for section in data['niobeClientData'][0][1]['data']['presentation']['stayProductDetailPage']['sections']['sbuiData']['sectionConfiguration']['root']['sections']:
        #     if section['sectionId'] == 'OVERVIEW_DEFAULT_V2':
        overview = data['niobeClientData'][0][1]['data']['node']['pdpPresentation']['overview']
        details['Название_2'] = overview.get('title', '').strip()
        details['Название_2_кратко'] = self._clean_object_type(overview.get('title', ''))
        overview_items = overview.get('items', [])
        details['Обзор'] = self._get_overview(overview_items)
        details['Обзор_элементы'] = overview_items
        details['Гостей'] = str(
            data['niobeClientData'][0][1]['data']['node']['pdpPresentation'].get('personCapacity', '')
            or ''
        )

        for key, default in {
            'Название': '',
            'Название_2': '',
            'Обзор': '',
            'Особенности': '',
            'Описание': '',
            'Удобства': '',
            'Изображения': [],
            'Локация': {},
            'Хозяин': {},
        }.items():
            details.setdefault(key, default)

        return details

    def _get_location(self, section):
        """Координаты с карты листинга (секция LOCATION_DEFAULT)."""
        sec = section.get('section') or {}
        lat, lng = sec.get('lat'), sec.get('lng')
        if lat is None or lng is None:
            return {}
        return {
            'latitude': lat,
            'longitude': lng,
            'subtitle': sec.get('subtitle', ''),
        }

    def _get_host(self, section):
        """Карточка хозяина (секция MEET_YOUR_HOST): имя, аватар, «о себе», хайлайты."""
        sec = section.get('section') or {}
        card = sec.get('cardData') or {}
        highlights = [
            (h.get('title') or '').strip()
            for h in (sec.get('hostHighlights') or [])
            if isinstance(h, dict) and h.get('title')
        ]
        return {
            'name': card.get('name', ''),
            'avatar_url': card.get('profilePictureUrl', ''),
            'is_superhost': bool(card.get('isSuperhost')),
            'about': (sec.get('about') or '').strip(),
            'highlights': highlights,
        }

    def _get_photos(self, section, data=None):
        items = []
        for item in (section.get('section') or {}).get('mediaItems') or []:
            url = item.get('baseUrl')
            if url:
                items.append(url)

        if data is not None:
            fallback = self._extract_photo_urls_from_data(data)
            if len(fallback) > len(items):
                items = fallback

        return items

    def _get_amenities(self, amenities_groups):
        items = []

        for item in amenities_groups:
            title = item['title']
            if title == "Не включено":
                break

            if title in self.AMENITIES_TO_SAVE_TITLE:
                items.append(title)
            for amenity in item['amenities']:
                items.append(amenity['title'])
                # if amenity['subtitle']:
                #     items.append(amenity['subtitle'])

        return '\n'.join(items)

    def _get_highlights(self, section):
        
        return ''
        # items = []

        # for item in section['section']['highlights']:
        #     if item['title'] in self.TARGET_HIGHLIGHTS:
        #         items.append(item['title'])
        #         subtitle = self._parse_highlights_subtitle(item['subtitle'])
        #         if subtitle:
        #             items.append(subtitle)

        # return '\n'.join(items)

    def _parse_highlights_subtitle(self, subtitle):
        subtitle = subtitle.replace(
            'Хозяин оборудовал жилье для длительной аренды. ', '')
        return subtitle

    def _clean_object_type(self, title):
        prefixes = (
            'Таунхаус целиком, ',
            'Жилье целиком, ',
            'Дом целиком, ',
            'Вилла целиком, ',
            'Квартира целиком, ',
        )
        for prefix in prefixes:
            title = title.replace(prefix, '')
        return title.strip()

    def _get_overview(self, overview_items):
        titles = []

        for item in overview_items:
            if isinstance(item, str):
                titles.append(item)
            elif isinstance(item, dict):
                title = item.get('title', '')
                if title:
                    titles.append(title)

        return ', '.join(titles)

    def _normalize_amount(self, value):
        cleaned = (
            str(value)
            .replace('&nbsp;', ' ')
            .replace('\xa0', ' ')
            .replace('\u00a0', ' ')
        )
        digits = re.sub(r'\D', '', cleaned)
        return digits if digits else ''

    def _parse_structured_display_price(self, price_obj):
        if not isinstance(price_obj, dict):
            return '', ''

        parts = []
        for key in ('primaryLine', 'secondaryLine', 'explanationData'):
            block = price_obj.get(key)
            if isinstance(block, dict):
                for field in ('price', 'qualifier', 'accessibilityLabel'):
                    text = block.get(field, '')
                    if text:
                        parts.append(str(text).strip())
            elif isinstance(block, str) and block.strip():
                parts.append(block.strip())

        display = ' '.join(parts).strip()
        amount = self._normalize_amount(display)
        if display and not self._accept_price_display(display):
            return '', ''
        return amount, display

    def _find_price_in_json(self, obj, depth=0, candidates=None):
        if candidates is None:
            candidates = []
        if depth > 25:
            return candidates

        if isinstance(obj, dict):
            structured = obj.get('structuredDisplayPrice')
            if structured:
                amount, display = self._parse_structured_display_price(structured)
                if amount or display:
                    candidates.append((amount, display))

            for key in ('displayPrice', 'priceString', 'totalPrice', 'total'):
                value = obj.get(key)
                if isinstance(value, str) and re.search(r'\d', value):
                    display = value.strip()
                    if self._accept_price_display(display):
                        amount = self._normalize_amount(display)
                        if amount:
                            candidates.append((amount, display))

            for value in obj.values():
                self._find_price_in_json(value, depth + 1, candidates)

        elif isinstance(obj, list):
            for item in obj:
                self._find_price_in_json(item, depth + 1, candidates)

        return candidates

    def _pick_best_json_price(self, candidates):
        best = ('', '')
        best_key = None
        for amount, display in candidates:
            if not display or not self._accept_price_display(display):
                continue
            parsed = self._parse_price_text(display)
            if not parsed.get('Цена'):
                continue
            key = self._price_sort_key(parsed, display)
            if best_key is None or key < best_key:
                best_key = key
                best = (parsed['Цена'], parsed.get('Цена_отображение') or display)
        return best

    def _find_price_in_json_text(self, page_source):
        candidates = []
        for match in re.finditer(r'"structuredDisplayPrice"\s*:\s*(\{.*?\})\s*,', page_source):
            try:
                price_obj = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            amount, display = self._parse_structured_display_price(price_obj)
            if amount or display:
                candidates.append((amount, display))
        return self._pick_best_json_price(candidates)

    def _detect_currency(self, text):
        text = str(text)
        if '฿' in text or re.search(r'\bTHB\b', text, re.I):
            return 'THB'
        if '$' in text or re.search(r'\bUSD\b', text, re.I):
            return 'USD'
        if '₽' in text or re.search(r'\bRUB\b|руб', text, re.I):
            return 'RUB'
        return ''

    def _accept_price_display(self, display, target_currency=None):
        target_currency = (target_currency or getattr(self, '_target_currency', None) or '').upper()
        if not display or len(str(display)) > 60:
            return False
        if re.search(r'Показать|Забронировать|Прибытие|Выезд|гость', str(display), re.I):
            return False
        if re.search(r'исходная\s+цена', str(display), re.I):
            return False
        detected = self._detect_currency(display)
        if detected == 'RUB':
            return False
        if target_currency:
            if not detected:
                return False
            return detected == target_currency
        return bool(detected)

    def _price_sort_key(self, parsed, raw_text=''):
        currency = parsed.get('Валюта') or self._detect_currency(raw_text)
        target = getattr(self, '_target_currency', None)
        target_penalty = 0 if (not target or currency == target) else 10
        monthly = 1 if re.search(r'помесячно|per month|monthly', raw_text, re.I) else 0
        return (
            target_penalty,
            self.CURRENCY_PRIORITY.get(currency, 5),
            -monthly,
        )

    def _select_best_price(self, candidates):
        best = {}
        best_key = None
        for raw_text, parsed in candidates:
            if not parsed.get('Цена'):
                continue
            display = parsed.get('Цена_отображение') or raw_text
            if not self._accept_price_display(display):
                continue
            key = self._price_sort_key(parsed, raw_text)
            if best_key is None or key < best_key:
                best_key = key
                best = parsed
        return best

    def _extract_price_from_dom(self):
        try:
            texts = self._ensure_sb().execute_script("""
                const out = [];
                for (const el of document.querySelectorAll('*')) {
                    const t = (el.textContent || '').trim();
                    if (!t || t.length > 80) continue;
                    if (t.includes('฿') || /RUB|USD|THB/i.test(t) || t.includes('₽') || t.includes('$') || /руб/i.test(t)) {
                        out.push(t);
                    }
                }
                return [...new Set(out)];
            """) or []
        except Exception:
            texts = []

        candidates = []
        for text in texts:
            if 'помесячно' not in text.lower() and 'month' not in text.lower():
                continue
            parsed = self._parse_price_text(text)
            if not parsed.get('Цена'):
                continue
            display = parsed.get('Цена_отображение') or text
            if self._accept_price_display(display):
                candidates.append((text, parsed))
        return self._select_best_price(candidates)

    def _extract_final_price(self, page_source, data, url_dates, url=''):
        target = url_dates.get('currency') or getattr(self, '_target_currency', None)
        if target:
            self._target_currency = target

        candidates = []
        amount, display = self._extract_price_from_html(page_source)
        if amount and display:
            parsed = self._parse_price_text(display)
            if parsed.get('Цена'):
                candidates.append((display, parsed))

        dom_price = self._extract_price_from_dom()
        if dom_price.get('Цена'):
            candidates.append((
                dom_price.get('Цена_отображение', ''),
                dom_price,
            ))

        if not candidates:
            json_candidates = self._find_price_in_json(data)
            amount, display = self._pick_best_json_price(json_candidates)
            if amount and display:
                parsed = self._parse_price_text(display)
                if not parsed.get('Цена'):
                    parsed = {'Цена': amount, 'Цена_отображение': display, 'Валюта': self._detect_currency(display)}
                if parsed.get('Цена') and self._accept_price_display(parsed.get('Цена_отображение', display)):
                    candidates.append((display, parsed))

        best = self._select_best_price(candidates)
        result = self._validate_price_fields(
            self._build_price_fields(best, url_dates) if best else {}
        )

        if result:
            return result

        if config.ENABLE_AI_PRICE_FALLBACK:
            ai_result = AiExtractor().extract_price(
                url=url,
                url_dates=url_dates,
                page_source=page_source,
                listing_title=data.get('Название', ''),
            )
            ai_result = self._validate_price_fields(ai_result)
            if ai_result:
                return ai_result

        return {}

    def _build_price_fields(self, parsed, url_dates):
        if not parsed or not parsed.get('Цена'):
            return {}
        display = parsed.get('Цена_отображение', '')
        period = url_dates.get('period', '')
        price_line = display
        if period and price_line:
            price_line = f'{price_line} ({period})'
        return {
            'Цена': parsed.get('Цена', ''),
            'Цена_отображение': display,
            'Цена_строка': price_line,
            'Период': period,
            'Дата_заезд': url_dates.get('check_in', ''),
            'Дата_выезд': url_dates.get('check_out', ''),
            'Цена_источник': 'parser',
        }

    def _parse_price_text(self, text):
        text = self._normalize_html_entities(text)
        if re.search(r'исходная\s+цена', text, re.I):
            text = re.split(r',?\s*исходная\s+цена', text, flags=re.I)[0]
        monthly = re.search(
            r'([\d\s,]+(?:฿|\$\s*USD|USD|RUB|₽)\s*помесячно)',
            text,
            re.IGNORECASE,
        )
        if monthly:
            text = monthly.group(1)
        text = re.sub(r'^[^0-9฿₽$]+', '', text)
        text = ' '.join(text.split())
        patterns = [
            (r'([\d\s\u00a0,]+)\s*฿(?:\s+([^\d,]+))?', 'THB'),
            (r'฿\s*([\d\s\u00a0,]+)(?:\s+([^\d,]+))?', 'THB'),
            (r'([\d\s\u00a0,]+)\s*\$\s*USD(?:\s+([^\d,]+))?', 'USD'),
            (r'\$\s*([\d\s\u00a0,]+)\s*USD(?:\s+([^\d,]+))?', 'USD'),
            (r'([\d\s\u00a0,]+)\s*USD(?:\s+([^\d,]+))?', 'USD'),
            (r'([\d\s\u00a0,]+)\s*RUB(?:\s+([^\d,]+))?', 'RUB'),
            (r'([\d\s\u00a0,]+)\s*₽(?:\s+([^\d,]+))?', 'RUB'),
            (r'([\d\s\u00a0,]+)\s*(?:руб\.?|рублей)(?:\s+([^\d,]+))?', 'RUB'),
            (r'(\d[\d\s\u00a0,]{2,})\s*(?:бат|THB)\b(?:\s+([^\d,]+))?', 'THB'),
        ]
        for pattern, currency in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            amount = self._normalize_amount(match.group(1))
            if not amount or int(amount) < 100:
                continue
            qualifier = (match.group(2) or '').strip() if match.lastindex and match.lastindex >= 2 else ''
            display = ' '.join(text.split())
            if qualifier and qualifier not in display:
                display = f'{match.group(0).strip()} {qualifier}'.strip()
            return {
                'Цена': amount,
                'Цена_отображение': display,
                'Цена_строка': display,
                'Валюта': currency,
            }
        return {}

    def _normalize_html_entities(self, text):
        return (
            str(text)
            .replace('&nbsp;', ' ')
            .replace('\xa0', ' ')
            .replace('\u00a0', ' ')
        )

    def _extract_price_from_html(self, page_source):
        normalized = self._normalize_html_entities(page_source)
        candidates = []

        for match in re.finditer(r'aria-label="([^"]+)"', normalized):
            label = match.group(1)
            if not re.search(r'USD|THB|RUB|฿|\$|₽|руб', label, re.I):
                continue
            if 'помесячно' not in label.lower() and 'month' not in label.lower():
                continue
            parsed = self._parse_price_text(label)
            if not parsed.get('Цена'):
                continue
            display = parsed.get('Цена_отображение', label)
            if self._accept_price_display(display):
                candidates.append((label, parsed))

        for match in re.finditer(
            r'([\d\s,]{3,})\s*(?:฿|USD|\$\s*USD|RUB|₽)\s*(?:помесячно)?',
            normalized,
            re.IGNORECASE,
        ):
            parsed = self._parse_price_text(match.group(0))
            if not parsed.get('Цена'):
                continue
            display = parsed.get('Цена_отображение', match.group(0))
            if self._accept_price_display(display):
                candidates.append((match.group(0), parsed))

        best = self._select_best_price(candidates)
        if best:
            return best['Цена'], best['Цена_отображение']
        return '', ''

    def _extract_price_info(self, data, page_source, url_dates):
        target = url_dates.get('currency') or getattr(self, '_target_currency', None)
        if target:
            self._target_currency = target

        amount, display = self._extract_price_from_html(page_source)
        if not amount and not display:
            candidates = self._find_price_in_json(data)
            amount, display = self._pick_best_json_price(candidates)
        if not amount and not display:
            amount, display = self._find_price_in_json_text(page_source)

        if not display or not self._accept_price_display(display):
            amount, display = '', ''

        period = url_dates.get('period', '')
        price_line = display

        result = {
            'Цена': amount if display else '',
            'Цена_отображение': display,
            'Цена_строка': price_line,
            'Период': period,
            'Дата_заезд': url_dates.get('check_in', ''),
            'Дата_выезд': url_dates.get('check_out', ''),
        }
        if period and price_line:
            result['Цена_строка'] = f'{price_line} ({period})'
        if not self._validate_price_fields(result, target):
            result['Цена'] = ''
            result['Цена_отображение'] = ''
            result['Цена_строка'] = ''
        return result


if __name__ == '__main__':
    # url = 'https://www.airbnb.ru/rooms/39403704?check_in=2026-02-05&check_out=2026-03-06&guests=1&adults=2&children=1&s=67&unique_share_id=f65fc33b-d00d-40a7-a839-63a0198efd74&source_impression_id=p3_1758815234_P3JLDxp9zxfKFZj0'
    url = 'https://www.airbnb.ru/l/nwhtWnQH'
    url = 'https://www.airbnb.ru/rooms/1660667866475609033?unique_share_id=36696733-33de-4a92-85fd-ffa6e02813c9&viralityEntryPoint=1&s=76'
    # url = 'https://www.airbnb.ru/rooms/1391145040784965609?check_in=2026-12-15&check_out=2027-01-15&guests=4&adults=4&pets=1&s=67&unique_share_id=7cb97769-573b-4695-9f59-3c345f2ee45e'
    url = 'https://www.airbnb.ru/rooms/39403704?viralityEntryPoint=1&s=76&source_impression_id=p3_1780058700_P3DVycU_sgLalsh5'

    parser = AirbnbParser(headless=False)
    text, media, data = parser.process_url(url)
    print('\n')
    print(text)
    print('\n')

