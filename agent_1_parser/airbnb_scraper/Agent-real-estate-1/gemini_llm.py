"""Gemini — LLM-мозги Агента 1 (голосовой поиск, чат, AI-фолбэки).

Заменяет Anthropic/Claude: ANTHROPIC_API_KEY больше не нужен.
"""

import json
import urllib.request

import config


def is_enabled() -> bool:
    return bool(config.GEMINI_API_KEY)


def generate(prompt: str = '', *, system: str = '', messages: list[dict] | None = None,
             max_tokens: int = 2048, timeout: int = 90) -> str:
    """Один вызов Gemini generateContent.

    messages — история [{'role': 'user'|'assistant', 'content': str}];
    prompt — добавляется последним user-сообщением (если задан).
    """
    if not config.GEMINI_API_KEY:
        raise RuntimeError('Нужен GEMINI_API_KEY в .env')

    contents = []
    for m in messages or []:
        role = 'model' if m.get('role') == 'assistant' else 'user'
        contents.append({'role': role, 'parts': [{'text': m.get('content', '')}]})
    if prompt:
        contents.append({'role': 'user', 'parts': [{'text': prompt}]})
    if not contents:
        raise ValueError('gemini_llm.generate: пустой запрос')

    body: dict = {
        'contents': contents,
        'generationConfig': {'maxOutputTokens': max_tokens},
    }
    if system:
        body['systemInstruction'] = {'parts': [{'text': system}]}

    url = (
        'https://generativelanguage.googleapis.com/v1beta/models/'
        f'{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}'
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)

    parts = (data.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
    text = ''.join(p.get('text', '') for p in parts).strip()
    if not text:
        raise RuntimeError(f'Gemini: пустой ответ ({json.dumps(data)[:300]})')
    return text
