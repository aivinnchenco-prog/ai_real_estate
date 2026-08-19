"""Cursor SDK — выполнение кодинг-задач из Telegram (/cursor)."""

import json
import os
from dataclasses import dataclass

import config
from CustomLogger import logger

SESSIONS_FILE = os.path.join(os.path.dirname(__file__), 'data', 'cursor_sessions.json')


@dataclass
class CursorRunResult:
    ok: bool
    text: str
    agent_id: str = ''
    run_id: str = ''
    status: str = ''
    error: str = ''


class CursorAgentRunner:
    def is_enabled(self) -> bool:
        return bool(config.CURSOR_API_KEY)

    def setup_hint(self) -> str:
        return (
            'Cursor SDK не настроен.\n'
            '1. Получи API key: cursor.com/dashboard/integrations\n'
            '2. Добавь в .env: CURSOR_API_KEY=cursor_...\n'
            '3. Перезапусти бота: python3 main.py'
        )

    def _load_sessions(self) -> dict:
        if not os.path.exists(SESSIONS_FILE):
            return {}
        try:
            with open(SESSIONS_FILE, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f'cursor sessions read error: {exc}')
            return {}

    def _save_sessions(self, data: dict) -> None:
        os.makedirs(os.path.dirname(SESSIONS_FILE) or '.', exist_ok=True)
        with open(SESSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_agent_id(self, user_id: int) -> str:
        return str(self._load_sessions().get(str(user_id), '')).strip()

    def set_agent_id(self, user_id: int, agent_id: str) -> None:
        data = self._load_sessions()
        if agent_id:
            data[str(user_id)] = agent_id
        else:
            data.pop(str(user_id), None)
        self._save_sessions(data)

    def clear_session(self, user_id: int) -> None:
        self.set_agent_id(user_id, '')

    def _collect_run_text(self, run) -> str:
        try:
            text = (run.text() or '').strip()
            if text:
                return text
        except Exception:
            pass

        parts = []
        try:
            for message in run.messages():
                if getattr(message, 'type', '') != 'assistant':
                    continue
                content = getattr(getattr(message, 'message', None), 'content', None) or []
                for block in content:
                    if getattr(block, 'type', '') == 'text':
                        parts.append(getattr(block, 'text', '') or '')
        except Exception as exc:
            logger.warning(f'cursor stream read error: {exc}')
        return ''.join(parts).strip()

    def run(self, user_id: int, prompt: str, *, new_session: bool = False) -> CursorRunResult:
        if not self.is_enabled():
            return CursorRunResult(ok=False, text='', error=self.setup_hint())

        prompt = (prompt or '').strip()
        if not prompt:
            return CursorRunResult(ok=False, text='', error='Пустой запрос.')

        from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

        project_dir = config.CURSOR_PROJECT_DIR
        context = (
            'Контекст проекта: Airbnb scraper — Python, aiogram 3, SeleniumBase, Google Sheets/Drive, '
            'Supabase task queue. Работай только в этой папке. Не предлагай Node.js/Playwright, '
            'если в проекте уже есть Python-модули.'
        )
        full_prompt = f'{context}\n\nЗадача пользователя:\n{prompt}'

        options = AgentOptions(
            api_key=config.CURSOR_API_KEY,
            model=config.CURSOR_MODEL,
            local=LocalAgentOptions(cwd=project_dir),
        )

        agent_id = '' if new_session else self.get_agent_id(user_id)
        if new_session:
            self.clear_session(user_id)

        try:
            if agent_id:
                try:
                    agent_cm = Agent.resume(agent_id, options)
                except Exception as exc:
                    logger.warning(f'Cursor resume failed ({agent_id}): {exc}')
                    agent_cm = Agent.create(options)
            else:
                agent_cm = Agent.create(options)

            with agent_cm as agent:
                saved_id = getattr(agent, 'agent_id', '') or getattr(agent, 'agentId', '')
                if saved_id:
                    self.set_agent_id(user_id, saved_id)

                run = agent.send(full_prompt)
                run_id = getattr(run, 'run_id', '') or getattr(run, 'id', '')
                logger.info(f'Cursor run: user={user_id} agent={saved_id} run={run_id}')

                result = run.wait()
                text = self._collect_run_text(run)
                if not text:
                    text = (getattr(result, 'result', '') or '').strip()

                status = str(getattr(result, 'status', '') or '')
                ok = status in ('finished', 'completed', 'done')
                error = '' if ok else (text or f'Статус: {status}')

                return CursorRunResult(
                    ok=ok,
                    text=text or ('Готово.' if ok else ''),
                    agent_id=saved_id,
                    run_id=run_id,
                    status=status,
                    error=error,
                )
        except CursorAgentError as exc:
            logger.error(f'Cursor SDK error: {exc}')
            retry = getattr(exc, 'is_retryable', False)
            hint = ' (можно повторить)' if retry else ''
            return CursorRunResult(ok=False, text='', error=f'Cursor SDK: {exc}{hint}')
        except Exception as exc:
            logger.error(f'Cursor run error: {exc}')
            return CursorRunResult(ok=False, text='', error=str(exc))
