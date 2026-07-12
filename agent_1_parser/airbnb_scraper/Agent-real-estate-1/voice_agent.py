import json
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import config
from CustomLogger import logger
from google_sheets import CRM_HEADERS, GoogleSheetsWriter

SEARCH_PAGE_SIZE = config.SEARCH_PAGE_SIZE
CHAT_HISTORY_LIMIT = config.CHAT_HISTORY_LIMIT
LEARNINGS_FILE = config.LEARNINGS_FILE
ACTIVITY_LOG_FILE = config.ACTIVITY_LOG_FILE
DEFAULT_RETRO_DAYS = config.DEFAULT_RETRO_DAYS

RETRO_TRIGGERS = (
    'ретроспектив', 'анализ работы', 'анализ периода', 'проделанной работ',
    'за неделю', 'за месяц', 'после недели', 'итоги недели', 'итоги работы',
    'как мы можем улучшить', 'как улучшить нашу систему', 'как улучшить систему',
    'что улучшить в системе', 'предложи улучшения', 'разбор работы',
)
CHAT_TRIGGERS = (
    'что ты умеешь', 'что умеешь', 'кто ты', 'привет', 'здравств',
    'помоги', 'help', 'jarvis', 'джарвис', 'как улучшить', 'предложи',
    'идея', 'совет', 'обсудим', 'расскажи о себе', 'что можешь',
    'чем помочь', 'как работаешь', 'улучшени', 'самообуч',
)
FEEDBACK_MARKERS = (
    'не то', 'неправильно', 'ошибка', 'не нашёл', 'не нашел',
    'плохой ответ', 'не так', 'не понял', 'не поняла', 'исправь',
)

FIELD_ALIASES = {
    'описание': 'Исходное описание',
    'описани': 'Исходное описание',
    'удобства': 'Все удобства',
    'контакт': 'Имя контакта',
    'имя контакта': 'Имя контакта',
    'whatsapp': 'WhatsApp',
    'ватсап': 'WhatsApp',
    'цена': 'Исходная цена',
    'ссылка': 'Ссылка на объявление',
    'ссылку': 'Ссылка на объявление',
    'заголовок': 'Заголовок',
    'район': 'Район',
    'локация': 'Локация / посёлок',
    'спальни': 'Спальни',
    'санузлы': 'Санузлы',
    'гостей': 'Гостей',
    'заметки': 'Заметки',
    'депозит': 'Депозит',
    'электричество': 'Электричество',
    'комиссия': 'Комиссия, %',
    'статус': 'Статус',
    'текст для fb': 'Текст для FB',
    'текст для tg': 'Текст для TG',
}

SEARCHABLE_COLUMNS = [
    'ID', 'Заголовок', 'Тип объекта', 'Район', 'Локация / посёлок',
    'Исходное описание', 'Все удобства', 'Заметки', 'Исходная цена',
    'Цена год-контракт', 'Спальни', 'Санузлы', 'Гостей', 'Бассейн',
]

search_sessions = {}
chat_sessions: dict[int, list[dict]] = {}
last_actions: dict[int, dict] = {}


class VoiceAgent:
    def __init__(self, sheets: GoogleSheetsWriter):
        self.sheets = sheets
        self._anthropic = None

    def is_llm_enabled(self):
        return bool(config.ANTHROPIC_API_KEY)

    def _client(self):
        if self._anthropic is None:
            import anthropic
            self._anthropic = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        return self._anthropic

    def parse_query(self, text: str) -> dict:
        if self._looks_like_retrospective(text):
            return {
                'intent': 'retrospective',
                'days': self._parse_period_days(text),
            }
        if self._looks_like_chat(text):
            return {'intent': 'chat'}
        if self.is_llm_enabled():
            try:
                return self._parse_with_claude(text)
            except Exception as e:
                logger.error(f'Claude parse error: {e}')
        return self._parse_simple(text)

    def _looks_like_retrospective(self, text: str) -> bool:
        text_lower = text.lower().replace('ё', 'е')
        if text_lower.startswith('/retro'):
            return True
        return any(trigger in text_lower for trigger in RETRO_TRIGGERS)

    def _parse_period_days(self, text: str) -> int:
        text_lower = text.lower().replace('ё', 'е')
        if text_lower.startswith('/retro'):
            parts = text_lower.split()
            if len(parts) > 1 and parts[1].isdigit():
                return max(1, min(int(parts[1]), 90))
        days_match = re.search(r'(\d+)\s*(?:дн|day)', text_lower)
        if days_match:
            return max(1, min(int(days_match.group(1)), 90))
        if '2 недел' in text_lower or 'две недел' in text_lower:
            return 14
        if 'месяц' in text_lower or '30 дн' in text_lower:
            return 30
        if 'недел' in text_lower or '7 дн' in text_lower:
            return 7
        return DEFAULT_RETRO_DAYS

    def _looks_like_chat(self, text: str) -> bool:
        text_lower = text.lower().replace('ё', 'е')
        if self._looks_like_retrospective(text):
            return False
        return any(trigger in text_lower for trigger in CHAT_TRIGGERS)

    def _parse_with_claude(self, text: str) -> dict:
        columns_list = ', '.join(CRM_HEADERS)
        system = f"""Ты роутер запросов к AI-агенту Jarvis (CRM недвижимости на Пхукете).
Колонки таблицы: {columns_list}

Верни ТОЛЬКО JSON без markdown:
{{
  "intent": "search" | "get_field" | "chat" | "retrospective",
  "object_id": "PHK-0001 или пустая строка",
  "field": "точное имя колонки или пустая строка",
  "filters": {{
    "district": "",
    "location": "",
    "keywords": [],
    "min_price": null,
    "max_price": null,
    "bedrooms": null,
    "bathrooms": null,
    "guests": null,
    "pool": null,
    "object_type": ""
  }}
}}

intent=search — найти объекты по критериям.
intent=get_field — прислать поле объекта (нужны object_id и field).
intent=chat — разговор, приветствие, «что умеешь», обычные идеи.
intent=retrospective — анализ работы за период, «как улучшить систему после недели», итоги, ретроспектива.

Цены в батах. "80-150 тысяч" = min_price 80000, max_price 150000.
"банг тао" = district. бассейн = pool true.
field — только точное имя из списка колонок."""

        response = self._client().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=1024,
            system=system,
            messages=[{'role': 'user', 'content': text}],
        )
        raw = ''
        for block in response.content:
            if block.type == 'text':
                raw += block.text
        raw = raw.strip()
        if raw.startswith('```'):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {'intent': 'search', 'filters': {'keywords': [text]}}

    def _parse_simple(self, text: str) -> dict:
        text_lower = text.lower().replace('ё', 'е')
        object_id_match = re.search(r'(PHK-\d+)', text, re.I)
        if object_id_match and any(w in text_lower for w in (
            'пришли', 'покажи', 'контакт', 'описание', 'колонк',
            'скинь', 'отправь', 'дай', 'найди',
        )):
            field = 'Исходное описание'
            for alias, column in FIELD_ALIASES.items():
                if alias in text_lower:
                    field = column
                    break
            return {
                'intent': 'get_field',
                'object_id': object_id_match.group(1).upper(),
                'field': field,
            }
        filters = {'keywords': []}
        if re.search(r'бассейн|pool', text_lower):
            filters['pool'] = True

        bedrooms = re.search(r'(\d+)\s*(?:спальн|комнат|bedroom)', text_lower)
        if bedrooms:
            filters['bedrooms'] = int(bedrooms.group(1))

        guests = re.search(r'(\d+)\s*(?:гост|guest)', text_lower)
        if guests:
            filters['guests'] = int(guests.group(1))

        raw_numbers = [int(p) for p in re.findall(r'(\d{2,6})', text_lower)]
        if 'тысяч' in text_lower or 'тыс' in text_lower:
            raw_numbers = [n * 1000 if n < 1000 else n for n in raw_numbers]
        if len(raw_numbers) >= 2:
            filters['min_price'] = min(raw_numbers)
            filters['max_price'] = max(raw_numbers)
        elif len(raw_numbers) == 1:
            filters['max_price'] = raw_numbers[0]

        districts = {
            'банг тао': 'банг тао',
            'бангтао': 'банг тао',
            'bang tao': 'банг тао',
            'лагуна': 'лагуна',
            'лагун': 'лагуна',
            'чернг талай': 'чернг талай',
            'чернгталай': 'чернг талай',
            'тхаланг': 'тхаланг',
            'пхукет': 'пхукет',
        }
        for key, district in districts.items():
            if key in text_lower:
                filters['district'] = district
                break

        if 'вилл' in text_lower:
            filters['object_type'] = 'вилла'
        elif 'дом' in text_lower:
            filters['object_type'] = 'дом'
        elif 'квартир' in text_lower:
            filters['object_type'] = 'квартира'

        if self._looks_like_chat(text):
            return {'intent': 'chat'}
        return {'intent': 'search', 'filters': filters}

    def _load_learnings(self) -> list[dict]:
        if not os.path.exists(LEARNINGS_FILE):
            return []
        try:
            with open(LEARNINGS_FILE, encoding='utf-8') as f:
                data = json.load(f)
            return data.get('learnings', [])[-15:]
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f'Не удалось прочитать learnings: {e}')
            return []

    def _save_learning(self, entry: dict):
        learnings = []
        if os.path.exists(LEARNINGS_FILE):
            try:
                with open(LEARNINGS_FILE, encoding='utf-8') as f:
                    learnings = json.load(f).get('learnings', [])
            except (OSError, json.JSONDecodeError):
                learnings = []

        entry['ts'] = datetime.now(timezone.utc).isoformat()
        learnings.append(entry)
        learnings = learnings[-100:]

        os.makedirs(os.path.dirname(LEARNINGS_FILE), exist_ok=True)
        with open(LEARNINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'learnings': learnings}, f, ensure_ascii=False, indent=2)

    def record_failed_search(self, user_id: int, query: str, filters: dict):
        self._save_learning({
            'type': 'no_results',
            'user_id': user_id,
            'query': query,
            'filters': filters,
        })
        last_actions[user_id] = {'type': 'failed_search', 'query': query, 'filters': filters}

    def record_feedback(self, user_id: int, text: str):
        prev = last_actions.get(user_id, {})
        self._save_learning({
            'type': 'user_feedback',
            'user_id': user_id,
            'feedback': text,
            'previous_action': prev,
        })

    def is_feedback(self, text: str) -> bool:
        text_lower = text.lower().replace('ё', 'е')
        return any(marker in text_lower for marker in FEEDBACK_MARKERS)

    def record_activity(self, user_id: int, action: str, **details):
        entry = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'user_id': user_id,
            'action': action,
        }
        entry.update(details)
        os.makedirs(os.path.dirname(ACTIVITY_LOG_FILE), exist_ok=True)
        with open(ACTIVITY_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def _load_activity(self, days: int, user_id: int | None = None) -> list[dict]:
        if not os.path.exists(ACTIVITY_LOG_FILE):
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        events = []
        try:
            with open(ACTIVITY_LOG_FILE, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if user_id is not None and event.get('user_id') != user_id:
                        continue
                    ts_raw = event.get('ts', '')
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace('Z', '+00:00'))
                    except ValueError:
                        continue
                    if ts >= cutoff:
                        events.append(event)
        except OSError as e:
            logger.error(f'Не удалось прочитать activity log: {e}')
        return events[-200:]

    def _build_period_stats(self, events: list[dict], days: int) -> str:
        if not events:
            return f'За последние {days} дн. в журнале нет записей — бот только начал собирать статистику.'

        by_action = Counter(event.get('action', 'unknown') for event in events)
        failed_queries = [
            event.get('query', '')
            for event in events
            if event.get('action') == 'search_fail' and event.get('query')
        ]
        success_queries = [
            f"{event.get('query', '')} → {event.get('result_count', 0)} шт."
            for event in events
            if event.get('action') == 'search_ok' and event.get('query')
        ]
        feedbacks = [
            event.get('feedback', '')
            for event in events
            if event.get('action') == 'feedback' and event.get('feedback')
        ]
        airbnb_added = [
            event.get('object_id', '')
            for event in events
            if event.get('action') == 'airbnb_parse' and event.get('success')
        ]

        lines = [
            f'Период: {days} дн. Событий: {len(events)}.',
            'По типам: ' + ', '.join(f'{name}={count}' for name, count in by_action.most_common()),
        ]
        if success_queries:
            lines.append('Удачные поиски: ' + '; '.join(success_queries[-10:]))
        if failed_queries:
            lines.append('Пустые поиски: ' + '; '.join(failed_queries[-10:]))
        if feedbacks:
            lines.append('Обратная связь: ' + '; '.join(feedbacks[-8:]))
        if airbnb_added:
            lines.append('Добавлено из Airbnb: ' + ', '.join(airbnb_added[-10:]))
        return '\n'.join(lines)

    def analyze_period(
        self,
        user_id: int,
        days: int,
        user_question: str = '',
        user_name: str = '',
    ) -> str:
        if not self.is_llm_enabled():
            return (
                f'Ретроспектива за {days} дн. недоступна без ANTHROPIC_API_KEY.\n'
                f'{self._build_period_stats(self._load_activity(days), days)}'
            )

        events = self._load_activity(days)
        period_stats = self._build_period_stats(events, days)
        learnings = [
            item for item in self._load_learnings()
            if self._learning_in_period(item, days)
        ]
        learning_lines = []
        for item in learnings[-15:]:
            if item.get('type') == 'no_results':
                learning_lines.append(f"- Пустой поиск: «{item.get('query', '')}»")
            elif item.get('type') == 'user_feedback':
                learning_lines.append(f"- Фидбек: «{item.get('feedback', '')}»")
        learnings_text = '\n'.join(learning_lines) or 'Нет отдельных уроков за период.'

        owner = user_name or 'владелец'
        question = user_question or (
            f'Как улучшить нашу систему после работы за последние {days} дней?'
        )
        prompt = f"""Запрос владельца ({owner}):
{question}

Журнал работы Jarvis за {days} дн.:
{period_stats}

Уроки и ошибки за период:
{learnings_text}

Статистика CRM сейчас:
{self.get_crm_summary()}

Сделай глубокий анализ и предложи улучшения по разделам:
1. Сводка периода — что реально происходило
2. Прибыль и деньги — как заработать больше на базе объектов
3. Упрощение и скорость — где убрать лишние шаги
4. CRM и данные — что улучшить в таблице и процессах
5. Jarvis/бот — что улучшить в поиске, ответах, командах
6. Быстрые победы — 3–5 действий на ближайшую неделю
7. Стратегия — идеи на 1–3 месяца

Опирайся только на данные журнала. Если данных мало — честно скажи и предложи что начать логировать.
Конкретика, без воды. По-русски, без HTML."""

        response = self._client().messages.create(
            model=config.ANTHROPIC_CHAT_MODEL,
            max_tokens=4096,
            system=(
                f'Ты {config.AGENT_NAME}, стратегический AI-советник по аренде недвижимости на Пхукете. '
                'Твоя задача — анализировать работу команды и предлагать улучшения для прибыли и простоты.'
            ),
            messages=[{'role': 'user', 'content': prompt}],
        )
        reply = ''
        for block in response.content:
            if block.type == 'text':
                reply += block.text
        reply = reply.strip() or 'Не удалось сформировать анализ. Попробуйте позже.'

        self.record_activity(user_id, 'retrospective', days=days, question=question[:500])
        last_actions[user_id] = {'type': 'retrospective', 'days': days}
        return reply

    def _learning_in_period(self, item: dict, days: int) -> bool:
        ts_raw = item.get('ts', '')
        if not ts_raw:
            return True
        try:
            ts = datetime.fromisoformat(ts_raw.replace('Z', '+00:00'))
        except ValueError:
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return ts >= cutoff

    def get_crm_summary(self) -> str:
        listings = self.get_all_listings()
        if not listings:
            return 'База пока пуста или недоступна.'

        districts = Counter(
            str(item.get('Район', '')).strip() or 'не указан'
            for item in listings
        )
        types = Counter(
            str(item.get('Тип объекта', '')).strip() or 'не указан'
            for item in listings
        )
        top_districts = ', '.join(f'{name} ({count})' for name, count in districts.most_common(5))
        top_types = ', '.join(f'{name} ({count})' for name, count in types.most_common(5))
        return (
            f'Всего объектов: {len(listings)}. '
            f'Топ районы: {top_districts}. '
            f'Типы: {top_types}.'
        )

    def _format_learnings_for_prompt(self) -> str:
        learnings = self._load_learnings()
        if not learnings:
            return 'Пока нет записанных уроков.'

        lines = []
        for item in learnings[-8:]:
            if item.get('type') == 'no_results':
                lines.append(f"- Пустой поиск: «{item.get('query', '')}»")
            elif item.get('type') == 'user_feedback':
                lines.append(f"- Обратная связь: «{item.get('feedback', '')}»")
        return '\n'.join(lines) if lines else 'Пока нет записанных уроков.'

    def _chat_system_prompt(self, user_name: str = '') -> str:
        owner = user_name or 'владелец'
        return f"""Ты {config.AGENT_NAME} — персональный AI-агент команды по аренде недвижимости на Пхукете.
Собеседник — {owner}, у него одна из крупнейших баз объектов долгосрочной аренды.

Твои возможности:
- Искать объекты в CRM по району, цене, спальням, бассейну и другим критериям
- Показывать поля объекта по ID (например PHK-0002: описание, контакт, WhatsApp)
- Парсить ссылки Airbnb и добавлять объекты в Google Sheets CRM_Объекты
- Обсуждать идеи по улучшению работы, CRM, бота и процессов
- Учиться на ошибках из журнала ниже и предлагать конкретные улучшения

Статистика базы: {self.get_crm_summary()}

Уроки из прошлых ошибок:
{self._format_learnings_for_prompt()}

Стиль: по-русски, умно, по делу, как Jarvis. Не выдумывай объекты и цифры — опирайся на статистику выше.
Если просят найти объект — подскажи пример запроса. Отвечай без HTML-тегов."""

    def chat(self, user_id: int, text: str, user_name: str = '') -> str:
        if not self.is_llm_enabled():
            return self._offline_chat_reply()

        history = chat_sessions.setdefault(user_id, [])
        history.append({'role': 'user', 'content': text})
        history[:] = history[-CHAT_HISTORY_LIMIT:]

        response = self._client().messages.create(
            model=config.ANTHROPIC_CHAT_MODEL,
            max_tokens=2048,
            system=self._chat_system_prompt(user_name),
            messages=history,
        )
        reply = ''
        for block in response.content:
            if block.type == 'text':
                reply += block.text
        reply = reply.strip() or self._offline_chat_reply()

        history.append({'role': 'assistant', 'content': reply})
        history[:] = history[-CHAT_HISTORY_LIMIT:]
        last_actions[user_id] = {'type': 'chat', 'query': text}
        return reply

    def _offline_chat_reply(self) -> str:
        return (
            f'Я {config.AGENT_NAME}, ваш помощник по базе аренды на Пхукете.\n\n'
            'Умею:\n'
            '• искать объекты в CRM\n'
            '• показывать поля по ID (PHK-0001)\n'
            '• парсить Airbnb-ссылки в таблицу\n\n'
            'Пример: «Вилла Банг Тао 3 спальни бассейн»\n'
            'Для полного диалога нужен ANTHROPIC_API_KEY в .env'
        )

    def get_all_listings(self):
        return self.sheets.get_all_listings()

    def search_listings(self, filters: dict) -> list[dict]:
        listings = self.get_all_listings()
        if not listings:
            return []

        scored = []
        for listing in listings:
            score = self._score_listing(listing, filters or {})
            if score > 0:
                scored.append((score, listing))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [listing for _, listing in scored]

    def _score_listing(self, listing: dict, filters: dict) -> int:
        score = 1
        blob = ' '.join(
            str(listing.get(col, '')).lower() for col in SEARCHABLE_COLUMNS
        )

        district = (filters.get('district') or '').lower()
        if district and district not in blob:
            return 0
        if district:
            score += 3

        location = (filters.get('location') or '').lower()
        if location and location not in blob:
            return 0
        if location:
            score += 2

        object_type = (filters.get('object_type') or '').lower()
        if object_type and object_type not in blob:
            return 0
        if object_type:
            score += 2

        for keyword in filters.get('keywords') or []:
            if keyword.lower() not in blob:
                return 0
            score += 1

        bedrooms = filters.get('bedrooms')
        if bedrooms is not None:
            listing_beds = self._parse_int(listing.get('Спальни', ''))
            if listing_beds is None or listing_beds < int(bedrooms):
                return 0
            score += 2

        bathrooms = filters.get('bathrooms')
        if bathrooms is not None:
            listing_baths = self._parse_int(listing.get('Санузлы', ''))
            if listing_baths is None or listing_baths < int(bathrooms):
                return 0
            score += 1

        guests = filters.get('guests')
        if guests is not None:
            listing_guests = self._parse_int(listing.get('Гостей', ''))
            if listing_guests is None or listing_guests < int(guests):
                return 0
            score += 1

        if filters.get('pool'):
            pool_text = ' '.join([
                str(listing.get('Бассейн', '')),
                str(listing.get('Все удобства', '')),
                str(listing.get('Исходное описание', '')),
            ]).lower()
            if 'бассейн' not in pool_text and 'pool' not in pool_text:
                return 0
            score += 2

        price = self._extract_price(listing)
        min_price = filters.get('min_price')
        max_price = filters.get('max_price')
        if min_price is not None or max_price is not None:
            if price is None:
                return 0
            if min_price is not None and price < int(min_price):
                return 0
            if max_price is not None and price > int(max_price):
                return 0
            score += 3

        return score

    def _parse_int(self, value) -> int | None:
        match = re.search(r'\d+', str(value))
        return int(match.group()) if match else None

    def _extract_price(self, listing: dict) -> int | None:
        for column in ('Исходная цена', 'Цена год-контракт', 'Цена для поста'):
            price = self._parse_int(listing.get(column, ''))
            if price is not None:
                return price
        return None

    def get_listing_field(self, object_id: str, field: str) -> str:
        listing = self.sheets.get_listing_by_id(object_id)
        if not listing:
            return ''

        field_name = FIELD_ALIASES.get(field.lower(), field)
        if field_name not in CRM_HEADERS:
            for header in CRM_HEADERS:
                if header.lower() == field_name.lower():
                    field_name = header
                    break

        return str(listing.get(field_name, '')).strip()

    def create_search_session(self, user_id: int, listings: list[dict], query_text: str) -> str:
        session_id = uuid.uuid4().hex[:12]
        search_sessions[session_id] = {
            'user_id': user_id,
            'listing_ids': [item.get('ID', '') for item in listings],
            'offset': 0,
            'query': query_text,
        }
        return session_id

    def get_search_page(self, session_id: str, user_id: int) -> tuple[list[dict], bool]:
        session = search_sessions.get(session_id)
        if not session or session['user_id'] != user_id:
            return [], False

        all_listings = {item['ID']: item for item in self.get_all_listings()}
        ids = session['listing_ids']
        offset = session['offset']
        page_ids = ids[offset:offset + SEARCH_PAGE_SIZE]
        page = [all_listings[obj_id] for obj_id in page_ids if obj_id in all_listings]

        new_offset = offset + SEARCH_PAGE_SIZE
        has_more = new_offset < len(ids)
        session['offset'] = new_offset
        return page, has_more

    def start_search_session(self, user_id: int, listings: list[dict], query_text: str) -> tuple[str, list[dict], bool]:
        session_id = self.create_search_session(user_id, listings, query_text)
        session = search_sessions[session_id]
        session['offset'] = SEARCH_PAGE_SIZE
        return session_id, listings[:SEARCH_PAGE_SIZE], len(listings) > SEARCH_PAGE_SIZE


def format_listing_card(listing: dict) -> str:
    lines = [f"<b>{listing.get('ID', '—')}</b>"]
    title = listing.get('Заголовок', '')
    if title:
        lines.append(title[:120])

    parts = []
    if listing.get('Район'):
        parts.append(listing['Район'])
    if listing.get('Локация / посёлок'):
        parts.append(listing['Локация / посёлок'])
    if parts:
        lines.append(' · '.join(parts))

    specs = []
    if listing.get('Спальни'):
        specs.append(f"{listing['Спальни']} сп.")
    if listing.get('Санузлы'):
        specs.append(f"{listing['Санузлы']} с/у")
    if listing.get('Гостей'):
        specs.append(f"{listing['Гостей']} гост.")
    if listing.get('Исходная цена'):
        specs.append(f"{listing['Исходная цена']} ฿")
    if specs:
        lines.append(' | '.join(specs))

    if listing.get('Бассейн'):
        lines.append(f"Бассейн: {listing['Бассейн']}")
    link = listing.get('Ссылка на объявление', '')
    if link:
        lines.append(f'<a href="{link}">Объявление</a>')
    return '\n'.join(lines)


def format_search_header(query_text: str, total: int, shown: int) -> str:
    return (
        f'🔍 <b>Найдено:</b> {total}\n'
        f'<i>Запрос:</i> {query_text}\n'
        f'Показано: {shown} из {total}'
    )


def help_text() -> str:
    return (
        f'<b>{config.AGENT_NAME}</b> — AI-агент вашей базы аренды на Пхукете\n\n'
        '💬 Диалог: «что ты умеешь», обсуждение идей\n'
        '📊 <b>Ретроспектива:</b>\n'
        '«Как улучшить систему после недели работы?»\n'
        'или /retro 7 — анализ за 7 дней\n\n'
        '🔍 <b>Поиск:</b>\n'
        '«Вилла Банг Тао 80–150 тысяч, 3 спальни, бассейн»\n\n'
        '📋 <b>Поле объекта:</b>\n'
        '«Пришли описание PHK-0002»\n\n'
        '🔗 <b>Airbnb / FB Marketplace:</b> отправьте ссылку — спарсит и передаст Агенту 2 в Notion CRM.'
    )
