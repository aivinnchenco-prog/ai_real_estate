import json
import re

import config  # noqa: F401 — конфиг подтягивает .env
import gemini_llm
from CustomLogger import logger


class ListingEnricher:
    def is_enabled(self):
        return gemini_llm.is_enabled()

    def fallback_texts(self, listing_data: dict, object_id: str = '', drive_url: str = '') -> dict:
        return self._fallback_texts(listing_data, object_id, drive_url)

    def enrich(self, listing_data: dict, object_id: str = '', drive_url: str = '') -> dict:
        """Возвращает {'Текст для FB': str, 'Текст для TG': str}."""
        if self.is_enabled():
            try:
                return self._enrich_with_llm(listing_data, object_id, drive_url)
            except Exception as exc:
                logger.error(f'enrich LLM error: {exc}')
        return self.fallback_texts(listing_data, object_id, drive_url)

    def _listing_summary(self, listing_data: dict, object_id: str, drive_url: str) -> str:
        lines = [
            f'ID: {object_id}' if object_id else '',
            f'Заголовок: {listing_data.get("Название", "")}',
            f'Тип: {listing_data.get("Название_2", "")}',
            f'Обзор: {listing_data.get("Обзор", "")}',
            f'Цена: {listing_data.get("Цена_строка") or listing_data.get("Цена_отображение", "")}',
            f'Период: {listing_data.get("Период", "")}',
            f'Особенности: {listing_data.get("Особенности", "")}',
            f'Описание: {(listing_data.get("Описание", "") or "")[:1500]}',
            f'Удобства: {(listing_data.get("Удобства", "") or "")[:800]}',
            f'Фото Drive: {drive_url}' if drive_url else '',
        ]
        return '\n'.join(line for line in lines if line)

    def _enrich_with_llm(self, listing_data: dict, object_id: str, drive_url: str) -> dict:
        summary = self._listing_summary(listing_data, object_id, drive_url)
        prompt = f"""Ты копирайтер по аренде недвижимости на Пхукете.

По данным объекта напиши ДВА поста на русском:
1) fb — для Facebook (до 1200 символов, эмодзи умеренно, призыв написать в WhatsApp)
2) tg — для Telegram-канала (до 900 символов, структурированно, эмодзи, хештеги #Пхукет #аренда)

Данные объекта:
{summary}

Ответь ТОЛЬКО JSON без markdown:
{{"fb": "...", "tg": "..."}}"""

        raw = gemini_llm.generate(prompt, max_tokens=2000)
        parsed = self._parse_json_response(raw)
        return {
            'Текст для FB': parsed.get('fb', '').strip(),
            'Текст для TG': parsed.get('tg', '').strip(),
        }

    def _parse_json_response(self, raw: str) -> dict:
        raw = raw.strip()
        if raw.startswith('```'):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)

    def _fallback_texts(self, listing_data: dict, object_id: str, drive_url: str) -> dict:
        title = listing_data.get('Название', 'Объект на Пхукете')
        overview = listing_data.get('Обзор', '')
        price = listing_data.get('Цена_строка') or listing_data.get('Цена_отображение', '')
        period = listing_data.get('Период', '')
        features = listing_data.get('Особенности', '')
        description = (listing_data.get('Описание', '') or '')[:600]

        price_line = f'\n💰 {price}' if price else ''
        period_line = f'\n📅 {period}' if period else ''
        id_line = f'\n🆔 {object_id}' if object_id else ''
        drive_line = f'\n📁 Фото: {drive_url}' if drive_url else ''

        fb = (
            f'🏝 {title}\n'
            f'{overview}{price_line}{period_line}\n\n'
            f'{features}\n\n'
            f'{description}'
            f'{id_line}{drive_line}\n\n'
            f'📲 Напишите в WhatsApp — подберём и покажем объект!'
        ).strip()

        tg = (
            f'<b>{title}</b>\n'
            f'{overview}{price_line}{period_line}\n\n'
            f'{features}\n\n'
            f'{description}'
            f'{id_line}{drive_line}\n\n'
            f'#Пхукет #аренда #вилла'
        ).strip()

        return {'Текст для FB': fb, 'Текст для TG': tg}
