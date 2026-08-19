from collections import Counter

import config
import gemini_llm
from google_sheets import GoogleSheetsWriter

CHAT_HISTORY_LIMIT = config.CHAT_HISTORY_LIMIT

chat_sessions: dict[int, list[dict]] = {}


class VoiceAgent:
    """Чат-помощник Jarvis (Gemini). Поиск по базе и ретроспектива удалены."""

    def __init__(self, sheets: GoogleSheetsWriter):
        self.sheets = sheets

    def is_llm_enabled(self):
        return gemini_llm.is_enabled()

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

    def _chat_system_prompt(self, user_name: str = '') -> str:
        owner = user_name or 'владелец'
        return f"""Ты {config.AGENT_NAME} — персональный AI-агент команды по аренде недвижимости на Пхукете.
Собеседник — {owner}, у него одна из крупнейших баз объектов долгосрочной аренды.

Твои возможности:
- Парсить ссылки Airbnb и Facebook Marketplace: объект попадает в CRM, дальше монтаж видео и публикация
- Запускать монтаж и публикацию по ID объекта (команды /agent3 и /agent4)
- Обсуждать идеи по улучшению работы, CRM, бота и процессов

Статистика базы: {self.get_crm_summary()}

Стиль: по-русски, умно, по делу, как Jarvis. Не выдумывай объекты и цифры — опирайся на статистику выше.
Отвечай без HTML-тегов."""

    def chat(self, user_id: int, text: str, user_name: str = '') -> str:
        if not self.is_llm_enabled():
            return self._offline_chat_reply()

        history = chat_sessions.setdefault(user_id, [])
        history.append({'role': 'user', 'content': text})
        history[:] = history[-CHAT_HISTORY_LIMIT:]

        reply = gemini_llm.generate(
            system=self._chat_system_prompt(user_name),
            messages=history,
            max_tokens=2048,
        ).strip() or self._offline_chat_reply()

        history.append({'role': 'assistant', 'content': reply})
        history[:] = history[-CHAT_HISTORY_LIMIT:]
        return reply

    def _offline_chat_reply(self) -> str:
        return (
            f'Я {config.AGENT_NAME}, ваш помощник по базе аренды на Пхукете.\n\n'
            'Умею:\n'
            '• парсить ссылки Airbnb / FB Marketplace в CRM\n'
            '• запускать монтаж и публикацию: /agent3, /agent4\n\n'
            'Для полного диалога нужен GEMINI_API_KEY в .env'
        )

    def get_all_listings(self):
        return self.sheets.get_all_listings()
