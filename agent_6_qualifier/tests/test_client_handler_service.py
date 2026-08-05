"""Unit tests for agent7.client_handler.process_client_message."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent6_qualifier.client_handler import ClientMessageTemplates, process_client_message
from agent6_qualifier.qualifier import Turn
from client_runtime_fixtures import FakeStore, make_client_session, make_event, make_sender, make_turn

NOTARY_CAPTION = (
    "Соглашение о бронировании (заявка). Оплаты по нему "
    "нет — итоговые условия зафиксируем в основном "
    "договоре после просмотра."
)


def _templates() -> ClientMessageTemplates:
    return ClientMessageTemplates(
        notary_caption=NOTARY_CAPTION,
        clients_folder="Клиенты",
        amo_stage_owner_request="Запрос владельцу",
        amo_stage_booking_confirmed="Бронь подтверждена",
    )


def _expected_manager_message(session, sender, text: str) -> str:
    uname = getattr(sender, "username", "") or ""
    who = f"@{uname}" if uname else f"chat_id={session.chat_id}"
    return (
        f"Клиент {who} ожидает ответа менеджера.\n"
        f"Объект: {session.lead.preferred_object_id or '-'}, "
        f"сделка #{session.amo_lead_id or '-'}\n"
        f"ФИО: {session.lead.full_name or '-'}, "
        f"гражданство: {session.lead.citizenship or '-'}\n"
        f"Сообщение: {text[:200]}"
    )


def build_process_env(
    *,
    session=None,
    turn: Turn | None = None,
    extract_return: dict | Exception | None = None,
    send_raises: Exception | None = None,
    folder_raises: Exception | None = None,
    manager_raises: Exception | None = None,
    save_raises: Exception | None = None,
):
    """Build process_client_message kwargs with a shared event log."""
    events: list[str] = []
    store = FakeStore()
    outreach_inflight: set[str] = set()
    notify_errors: list[tuple] = []
    outreach_calls: list = []
    notary_calls: list = []
    manager_calls: list = []
    outreach_tasks: list = []
    real_create_task = asyncio.create_task

    if session is None:
        session = make_client_session()
    store._by_chat[session.chat_id] = session

    if turn is None:
        turn = make_turn()

    def _get_session(chat_id: str):
        events.append("get_session")
        return session

    def _extract(text, sess):
        events.append("extract")
        if isinstance(extract_return, Exception):
            raise extract_return
        if extract_return is not None:
            return extract_return
        return {}

    def _handle(sess, text, update):
        events.append("handle_message")
        return turn

    def _polish(draft, language, name):
        events.append("polish")
        return draft

    async def _send(event, reply):
        events.append("client_reply")
        if send_raises:
            raise send_raises

    async def _notary(sess, contact, **kwargs):
        events.append("notary")
        notary_calls.append((sess, contact, kwargs))
        result = MagicMock()
        result.sent_to_client = False
        result.doc_path = None
        result.attached_to_amo = False
        return result

    def _generate_doc(*args, **kwargs):
        events.append("generate_doc")
        return Path("/tmp/fake.docx")

    async def _folder(client, sender, folder):
        events.append("folder")
        if folder_raises:
            raise folder_raises

    def _ensure_amo(amo, sess, sender):
        events.append("ensure_amo_lead")

    def _notify_manager(text, *, dedup_key=""):
        events.append("manager")
        manager_calls.append((text, dedup_key))
        if manager_raises:
            raise manager_raises

    async def _auto_outreach(client, sess, st, amo):
        events.append("auto_outreach")
        outreach_calls.append((client, sess, st, amo))

    def _create_task(coro, **kwargs):
        task = real_create_task(coro, **kwargs)
        if getattr(coro, "cr_code", None) and coro.cr_code.co_name == "_run_outreach":
            outreach_tasks.append(task)
        return task

    amo = MagicMock()
    amo.ensure_pipeline = MagicMock(
        side_effect=lambda: (
            events.append("amo_pipeline") or {
                "Запрос владельцу": 11,
                "Бронь подтверждена": 22,
            }
        ),
    )
    amo.ensure_lead_fields = MagicMock(return_value={})
    amo.update_lead_fields = MagicMock(side_effect=lambda *a, **k: events.append("amo_fields"))
    amo.update_lead_status = MagicMock(side_effect=lambda *a, **k: events.append("amo_status"))
    amo.note_client = MagicMock(side_effect=lambda *a, **k: events.append("amo_note"))
    amo.attach_file = MagicMock()

    client = MagicMock()
    client.send_file = AsyncMock()

    event = make_event()
    sender = make_sender()

    _orig_save = store.save

    def _save_wrapped(sess):
        events.append("save")
        if save_raises:
            _orig_save(sess)
            raise save_raises
        _orig_save(sess)

    store.save = _save_wrapped  # type: ignore[method-assign]

    kwargs = dict(
        event=event,
        sender=sender,
        client=client,
        amo=amo,
        chat_id=session.chat_id,
        store=store,
        outreach_inflight=outreach_inflight,
        get_session=_get_session,
        extract_lead_update=_extract,
        handle_message=_handle,
        polish_reply=_polish,
        send_client_response=_send,
        process_confirmed_booking=_notary,
        generate_booking_doc=_generate_doc,
        add_to_folder=_folder,
        ensure_amo_lead=_ensure_amo,
        notify_manager=_notify_manager,
        notify_error=lambda c, e, ctx="": notify_errors.append((c, e, ctx)),
        auto_outreach=_auto_outreach,
        create_task=_create_task,
        templates=_templates(),
    )
    return kwargs, events, store, outreach_inflight, notify_errors, amo, outreach_tasks, manager_calls


def run_process(**build_kwargs):
    kwargs, events, store, inflight, notify_errors, amo, outreach_tasks, manager_calls = (
        build_process_env(**build_kwargs)
    )
    asyncio.run(process_client_message(**kwargs))
    return events, store, inflight, notify_errors, amo, outreach_tasks, manager_calls


async def drain_outreach(outreach_tasks):
    for task in list(outreach_tasks):
        await task


def test_normal_flow_side_effect_order():
    session = make_client_session()
    turn = make_turn(skip_polish=True, events=["viewing scheduled"])
    kwargs, events, store, _, _, _, outreach_tasks, _ = build_process_env(
        session=session,
        turn=turn,
        extract_return={"full_name": "Ivan"},
    )
    asyncio.run(process_client_message(**kwargs))

    assert events == [
        "get_session",
        "extract",
        "handle_message",
        "client_reply",
        "folder",
        "ensure_amo_lead",
        "amo_fields",
        "amo_note",
        "save",
    ]
    assert len(store.saved) == 1
    assert session.history[-2]["role"] == "user"
    assert session.history[-1]["role"] == "assistant"
    assert outreach_tasks == []


def test_extract_failure_continues_with_empty_update():
    events, store, _, notify_errors, _, _, _ = run_process(
        extract_return=RuntimeError("gemini down"),
    )

    assert any(e[0] == "gemini.extract" for e in notify_errors)
    assert "extract" in events
    assert "handle_message" in events
    assert "client_reply" in events
    assert "save" in events
    assert len(store.saved) == 1


def test_skip_polish_skips_polish():
    turn = make_turn(skip_polish=True)
    events, _, _, _, _, _, _ = run_process(turn=turn)
    assert "polish" not in events

    turn_polish = make_turn(skip_polish=False)
    kwargs, events, _, _, _, _, _, _ = build_process_env(turn=turn_polish)
    asyncio.run(process_client_message(**kwargs))
    assert "polish" in events


def test_notary_gate_runs_after_client_reply():
    turn = make_turn(booking_confirmed=True, skip_polish=True)
    events, _, _, _, _, _, _ = run_process(turn=turn)
    assert events.index("client_reply") < events.index("notary")
    assert events.index("notary") < events.index("save")


def test_handoff_gate_calls_notify_manager():
    turn = make_turn(handoff_to_human=True, skip_polish=True)
    session = make_client_session()
    kwargs, events, _, _, _, _, _, manager_calls = build_process_env(
        session=session,
        turn=turn,
    )
    event = kwargs["event"]
    sender = kwargs["sender"]
    event.raw_text = "Жду менеджера"
    asyncio.run(process_client_message(**kwargs))

    assert "manager" in events
    text, dedup_key = manager_calls[0]
    assert dedup_key == session.chat_id
    assert text == _expected_manager_message(session, sender, "Жду менеджера")


def test_envoy_gate_and_inflight_lifecycle():
    turn = make_turn(need_owner_check=True, skip_polish=True)
    kwargs, events, store, inflight, _, _, outreach_tasks, _ = build_process_env(turn=turn)

    async def _slow_auto_outreach(client, sess, st, amo):
        events.append("auto_outreach")
        await asyncio.sleep(0.02)

    kwargs["auto_outreach"] = _slow_auto_outreach

    async def _run():
        task = asyncio.create_task(process_client_message(**kwargs))
        await asyncio.sleep(0.005)
        assert kwargs["chat_id"] in inflight
        await task
        for outreach_task in outreach_tasks:
            await outreach_task
        assert kwargs["chat_id"] not in inflight

    asyncio.run(_run())
    assert "amo_status" in events
    assert len(outreach_tasks) == 1
    assert "auto_outreach" in events
    assert events.index("save") > events.index("amo_status")
    assert len(store.saved) == 1


def test_booking_confirmed_sets_amo_stage():
    turn = make_turn(booking_confirmed=True, skip_polish=True)
    events, _, _, _, amo, _, _ = run_process(turn=turn)
    assert events.count("amo_status") >= 1
    amo.update_lead_status.assert_called()
    status_calls = [c.args[1] for c in amo.update_lead_status.call_args_list]
    assert 22 in status_calls


def test_client_reply_error_blocks_subsequent_side_effects():
    kwargs, events, store, _, _, amo, _, _ = build_process_env(
        send_raises=RuntimeError("tg down"),
    )
    with pytest.raises(RuntimeError, match="tg down"):
        asyncio.run(process_client_message(**kwargs))

    assert "client_reply" in events
    assert "folder" not in events
    assert "save" not in events
    assert store.saved == []
    amo.update_lead_fields.assert_not_called()


def test_folder_error_blocks_amo_and_save():
    kwargs, events, store, _, _, amo, _, _ = build_process_env(
        folder_raises=RuntimeError("folder down"),
    )
    with pytest.raises(RuntimeError, match="folder down"):
        asyncio.run(process_client_message(**kwargs))

    assert "folder" in events
    assert "ensure_amo_lead" not in events
    assert "save" not in events
    assert store.saved == []
    amo.ensure_pipeline.assert_not_called()


def test_notify_manager_error_blocks_outreach_and_save():
    turn = make_turn(handoff_to_human=True, need_owner_check=True, skip_polish=True)
    kwargs, events, store, inflight, _, _, outreach_tasks, _ = build_process_env(
        turn=turn,
        manager_raises=RuntimeError("manager down"),
    )
    with pytest.raises(RuntimeError, match="manager down"):
        asyncio.run(process_client_message(**kwargs))

    assert "manager" in events
    assert "save" not in events
    assert store.saved == []
    assert outreach_tasks == []
    assert kwargs["chat_id"] not in inflight


def test_create_task_failure_leaves_inflight_and_skips_save():
    turn = make_turn(need_owner_check=True, skip_polish=True)
    kwargs, events, store, inflight, _, _, outreach_tasks, _ = build_process_env(
        turn=turn,
    )

    def _create_task_raises(coro, **kwargs):
        events.append("create_task")
        coro.close()
        raise RuntimeError("task creation failed")

    kwargs["create_task"] = _create_task_raises

    with pytest.raises(RuntimeError, match="task creation failed"):
        asyncio.run(process_client_message(**kwargs))

    chat_id = kwargs["chat_id"]
    assert chat_id in inflight
    assert "amo_status" in events
    assert events.index("amo_status") < events.index("create_task")
    assert "save" not in events
    assert store.saved == []
    assert outreach_tasks == []
    assert "auto_outreach" not in events


def test_history_trim_keeps_last_twelve_entries():
    session = make_client_session()
    session.history = [{"role": "user", "text": f"old-{i}"} for i in range(12)]
    turn = make_turn(skip_polish=True)
    kwargs, events, store, _, _, _, _, _ = build_process_env(
        session=session,
        turn=turn,
    )
    event = kwargs["event"]
    event.raw_text = "new user message"
    asyncio.run(process_client_message(**kwargs))

    assert len(session.history) == 12
    assert session.history[-2] == {"role": "user", "text": "new user message"}
    assert session.history[-1] == {
        "role": "assistant",
        "text": turn.reply_draft,
    }
    assert session.history[0] == {"role": "user", "text": "old-2"}
    assert session.history[1] == {"role": "user", "text": "old-3"}
    assert {"role": "user", "text": "old-0"} not in session.history
    assert {"role": "user", "text": "old-1"} not in session.history
    assert "save" in events


def test_save_error_after_outreach_task_clears_inflight():
    turn = make_turn(need_owner_check=True, skip_polish=True)
    kwargs, events, store, inflight, notify_errors, _, outreach_tasks, _ = build_process_env(
        turn=turn,
        save_raises=RuntimeError("disk full"),
    )
    chat_id = kwargs["chat_id"]
    with pytest.raises(RuntimeError, match="disk full"):
        asyncio.run(process_client_message(**kwargs))

    assert len(outreach_tasks) == 1
    asyncio.run(drain_outreach(outreach_tasks))
    assert chat_id not in inflight
    assert len(store.saved) == 1


_CLIENT_HANDLER_IMPORT_SCRIPT = """
import builtins
import os
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec

os.chdir(sys.argv[1])

import requests

def _block_network(*args, **kwargs):
    raise AssertionError("network call during import")

requests.get = requests.post = requests.request = _block_network

_real_open = builtins.open

def _guarded_open(file, *args, **kwargs):
    path = os.fspath(file)
    if path.endswith(".env") or path.endswith(os.sep + ".env"):
        raise AssertionError(".env read during import")
    return _real_open(file, *args, **kwargs)

builtins.open = _guarded_open

BLOCKED_EXACT = {"agent6_qualifier.tg_userbot"}
BLOCKED_PREFIXES = ("telethon",)

class _ForbiddenLoader(Loader):
    def __init__(self, name):
        self._name = name

    def create_module(self, spec):
        raise AssertionError(f"forbidden import: {self._name}")

    def exec_module(self, module):
        raise AssertionError(f"forbidden import: {self._name}")

class _ForbiddenFinder(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in BLOCKED_EXACT:
            return ModuleSpec(fullname, _ForbiddenLoader(fullname))
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in BLOCKED_PREFIXES):
            return ModuleSpec(fullname, _ForbiddenLoader(fullname))
        return None

sys.meta_path.insert(0, _ForbiddenFinder())

_real_import = builtins.__import__

def _import_with_sessionstore_guard(name, globals=None, locals=None, fromlist=(), level=0):
    mod = _real_import(name, globals, locals, fromlist, level)
    if name == "agent6_qualifier.sessions" and hasattr(mod, "SessionStore"):
        def _blocked_init(self, *args, **kwargs):
            raise AssertionError("SessionStore instantiated during import")

        mod.SessionStore.__init__ = _blocked_init
    return mod

builtins.__import__ = _import_with_sessionstore_guard

from agent6_qualifier.client_handler import ClientMessageTemplates, process_client_message

assert callable(process_client_message)
assert ClientMessageTemplates is not None
print("OK")
"""


def test_client_handler_imports_without_side_effects(tmp_path):
    """client_handler must import in a clean subprocess without forbidden side effects."""
    src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src)

    result = subprocess.run(
        [sys.executable, "-c", _CLIENT_HANDLER_IMPORT_SCRIPT, str(tmp_path)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "OK" in result.stdout
    assert list(tmp_path.iterdir()) == []
