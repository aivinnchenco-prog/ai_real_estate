"""Gemini — запасной извлекатель данных, когда парсер не уверен."""

import json
import re

import config
import gemini_llm
from CustomLogger import logger


class AiExtractor:
    def is_enabled(self):
        return gemini_llm.is_enabled()

    def _parse_json_response(self, raw: str) -> dict:
        raw = raw.strip()
        if raw.startswith('```'):
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)

    def _collect_price_snippets(self, page_source: str, max_items: int = 20) -> list[str]:
        normalized = (
            str(page_source)
            .replace('&nbsp;', ' ')
            .replace('\xa0', ' ')
        )
        snippets = []

        for match in re.finditer(r'aria-label="([^"]+)"', normalized):
            label = match.group(1)
            if re.search(r'USD|THB|RUB|฿|\$|₽|руб|помесячно|month|price', label, re.I):
                snippets.append(label.strip())

        for match in re.finditer(
            r'"structuredDisplayPrice"\s*:\s*(\{.*?\})\s*,',
            page_source,
        ):
            snippets.append(match.group(1)[:500])

        for match in re.finditer(
            r'"(?:displayPrice|priceString|totalPrice)"\s*:\s*"([^"]+)"',
            page_source,
        ):
            text = match.group(1).strip()
            if re.search(r'\d', text):
                snippets.append(text)

        unique = []
        seen = set()
        for item in snippets:
            if item not in seen:
                seen.add(item)
                unique.append(item)
            if len(unique) >= max_items:
                break
        return unique

    def extract_price(
        self,
        url: str,
        url_dates: dict,
        page_source: str = '',
        listing_title: str = '',
    ) -> dict:
        """
        Возвращает поля цены или {}.
        Вызывается только когда обычный парсер не справился.
        """
        if not self.is_enabled():
            return {}

        target_currency = (url_dates.get('currency') or config.AIRBNB_CURRENCY or 'THB').upper()
        snippets = self._collect_price_snippets(page_source)
        if not snippets and not url:
            return {}

        prompt = f"""Ты извлекаешь цену аренды с Airbnb для CRM.

URL: {url}
Заезд: {url_dates.get('check_in', '')}
Выезд: {url_dates.get('check_out', '')}
Период: {url_dates.get('period', '')}
Нужная валюта: {target_currency}
Заголовок: {listing_title}

Фрагменты со страницы (цены, aria-label, JSON):
{chr(10).join(f'- {s}' for s in snippets) if snippets else '(фрагментов нет)'}

Правила:
1. Верни месячную цену за указанный период (помесячно), не исходную/зачёркнутую.
2. Валюта СТРОГО {target_currency}. RUB/рубли — игнорируй.
3. Если в данных только RUB — amount пустой.
4. display — как на сайте, с символом валюты, например "5 670 $ USD помесячно" или "186 048 ฿ помесячно".

Ответь ТОЛЬКО JSON:
{{"amount": "5670", "display": "5 670 $ USD помесячно", "currency": "USD", "confidence": "high"}}
amount — только цифры без пробелов."""

        try:
            raw = gemini_llm.generate(prompt, max_tokens=400)
            parsed = self._parse_json_response(raw)
        except Exception as exc:
            logger.error(f'AI price extract error: {exc}')
            return {}

        currency = str(parsed.get('currency', '')).upper()
        display = str(parsed.get('display', '')).strip()
        amount = re.sub(r'\D', '', str(parsed.get('amount', '')))
        confidence = str(parsed.get('confidence', '')).lower()

        if currency and currency != target_currency:
            logger.warning(f'AI price: валюта {currency} != {target_currency}, пропуск')
            return {}
        if re.search(r'RUB|₽|руб', display, re.I):
            return {}
        if not amount or int(amount) < 100:
            return {}
        if confidence in ('low', 'none', '0'):
            return {}

        period = url_dates.get('period', '')
        price_line = display
        if period and price_line:
            price_line = f'{price_line} ({period})'

        logger.info(f'AI price: {display} ({target_currency})')
        return {
            'Цена': amount,
            'Цена_отображение': display,
            'Цена_строка': price_line,
            'Период': period,
            'Дата_заезд': url_dates.get('check_in', ''),
            'Дата_выезд': url_dates.get('check_out', ''),
            'Цена_источник': 'ai',
        }
