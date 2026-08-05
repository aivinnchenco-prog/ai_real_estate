"""Unit tests for agent7_envoy.owner_handler.process_owner_message."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7.models import Availability
from agent7_envoy.owner_handler import OwnerMessageTemplates, process_owner_message
from agent7_envoy.owner_result import (
    OwnerVerdict,
    apply_verdict_to_session,
    build_client_message,
    notion_availability_update,
)
from owner_handler_fixtures import FakeStore, make_event, make_owner_session, make_sender

_UNSET = object()


def _templates() -> OwnerMessageTemplates:
    from agent7.templates import OWNER_ACK_CONDITIONS, OWNER_ACK_FREE, OWNER_BUSY_FOLLOWUP
    return OwnerMessageTemplates(
        ack_free=OWNER_ACK_FREE,
        ack_conditions=OWNER_ACK_CONDITIONS,
        busy_followup=OWNER_BUSY_FOLLOWUP,
    )


def build_process_env(
    *,
    session=None,
    verdict: OwnerVerdict | None = None,
    get_owner_return=_UNSET,
    notion_raises: Exception | None = None,
    client_send_raises: Exception | None = None,
    amo_raises: Exception | None = None,
):
    """Build process_owner_message kwargs with a shared event log."""
    events: list[str] = []
    store = FakeStore(session)
    sessions_cache: dict = {}
    notify_errors: list[tuple] = []

    if verdict is None:
        verdict = OwnerVerdict(status="free")

    def _mark_owner(**kwargs):
        events.append("mark_owner")

    def _get_owner(username, owner_chat_id):
        events.append("get_owner")
        if get_owner_return is not _UNSET:
            return get_owner_return
        return {"object_id": "A_20260713_003"}

    def _parse(_text, _session):
        events.append("parse")
        return verdict

    def _build(v, s):
        events.append("build_client_message")
        return build_client_message(v, s)

    def _apply(s, v):
        events.append("apply_verdict")
        apply_verdict_to_session(s, v)

    def _notion_map(v):
        events.append("notion_map")
        return notion_availability_update(v)

    def _notion_update(page_id, status, *, busy_until=None, future_bookings=""):
        events.append("notion_update")
        if notion_raises:
            raise notion_raises

    def _polish(draft, language, name):
        events.append("polish")
        return draft

    async def _owner_response(event, text):
        events.append("owner_response")

    async def _client_send(chat_id, reply):
        events.append("client_send")
        if client_send_raises:
            raise client_send_raises

    _orig_save = store.save

    def _save_wrapped(session):
        events.append("save")
        _orig_save(session)

    store.save = _save_wrapped  # type: ignore[method-assign]

    async def _folder(client, sender, folder):
        events.append("folder")

    amo = MagicMock()
    amo.ensure_pipeline = MagicMock(side_effect=lambda: (events.append("amo_pipeline") or {"Согласование условий": 555}))
    amo.update_lead_status = MagicMock(side_effect=lambda *a, **k: events.append("amo_status"))
    amo.note_owner = MagicMock(side_effect=lambda *a, **k: events.append("amo_note_owner"))
    amo.note_client = MagicMock(side_effect=lambda *a, **k: events.append("amo_note_client"))
    if amo_raises:
        amo.ensure_pipeline.side_effect = amo_raises

    client = MagicMock()

    kwargs = dict(
        client=client,
        event=make_event(),
        sender=make_sender(),
        amo=amo,
        store=store,
        sessions_cache=sessions_cache,
        get_owner=_get_owner,
        mark_owner=_mark_owner,
        parse_owner_reply=_parse,
        build_client_message=_build,
        apply_verdict_to_session=_apply,
        notion_availability_update=_notion_map,
        update_notion_availability=_notion_update,
        polish_reply=_polish,
        send_owner_response=_owner_response,
        send_client_message=_client_send,
        add_to_folder=_folder,
        notify_error=lambda c, e, ctx="": notify_errors.append((c, e, ctx)),
        owners_folder="Собственники",
        templates=_templates(),
    )
    return kwargs, events, store, sessions_cache, notify_errors, amo


def run_process(**build_kwargs):
    kwargs, events, store, sessions_cache, notify_errors, amo = build_process_env(**build_kwargs)
    result = asyncio.run(process_owner_message(**kwargs))
    return result, events, store, sessions_cache, notify_errors, amo


def test_free_flow_side_effect_order():
    session = make_owner_session()
    result, events, store, sessions_cache, _, _ = run_process(session=session)

    assert result is True
    assert events == [
        "get_owner",
        "mark_owner",
        "parse",
        "owner_response",
        "build_client_message",
        "polish",
        "apply_verdict",
        "notion_map",
        "notion_update",
        "client_send",
        "save",
        "folder",
        "amo_pipeline",
        "amo_status",
        "amo_note_owner",
        "amo_note_client",
    ]
    assert len(store.saved) == 1
    assert sessions_cache[session.chat_id] is session


def test_busy_without_date_early_exit():
    session = make_owner_session()
    result, events, store, sessions_cache, _, _ = run_process(
        session=session,
        verdict=OwnerVerdict(status="busy"),
    )

    assert result is True
    assert events == ["get_owner", "mark_owner", "parse", "owner_response"]
    assert store.saved == []
    assert sessions_cache == {}
    assert session.awaiting_owner is True


def test_unknown_sender_returns_false():
    result, events, store, sessions_cache, _, _ = run_process(
        session=None,
        get_owner_return=None,
    )

    assert result is False
    assert events == ["get_owner"]
    assert store.saved == []
    assert sessions_cache == {}


def test_session_lookup_fallback_by_registry_object_id():
    session = make_owner_session(owner_telegram="")
    kwargs, events, store, sessions_cache, _, _ = build_process_env(session=session)

    def _find_user(_username):
        events.append("find_user")
        return None

    store.find_awaiting_owner = _find_user  # type: ignore[method-assign]
    result = asyncio.run(process_owner_message(**kwargs))

    assert result is True
    assert "find_user" in events
    assert "client_send" in events


def test_notion_failure_does_not_block_client_or_save():
    session = make_owner_session()
    result, events, store, _, notify_errors, _ = run_process(
        session=session,
        notion_raises=RuntimeError("notion down"),
    )

    assert result is True
    assert any(e[0] == "notion.availability" for e in notify_errors)
    idx_notion = events.index("notion_update")
    idx_client = events.index("client_send")
    idx_save = events.index("save")
    assert idx_notion < idx_client < idx_save


def test_amo_failure_happens_after_client_and_save():
    session = make_owner_session()
    result, events, store, _, notify_errors, _ = run_process(
        session=session,
        amo_raises=RuntimeError("amo down"),
    )

    assert result is True
    assert any(e[0] == "amo.owner_flow" for e in notify_errors)
    idx_save = events.index("save")
    idx_folder = events.index("folder")
    assert events.index("client_send") < idx_save < idx_folder
    assert "amo_pipeline" not in events


def test_client_send_error_prevents_save_folder_and_amo():
    session = make_owner_session()
    kwargs, events, store, sessions_cache, _, amo = build_process_env(
        session=session,
        client_send_raises=RuntimeError("tg down"),
    )

    with pytest.raises(RuntimeError, match="tg down"):
        asyncio.run(process_owner_message(**kwargs))

    assert "apply_verdict" in events
    assert "client_send" in events
    assert "save" not in events
    assert "folder" not in events
    assert "amo_pipeline" not in events
    assert store.saved == []
    assert sessions_cache == {}
    amo.ensure_pipeline.assert_not_called()


def test_save_error_prevents_folder_and_amo():
    session = make_owner_session()
    kwargs, events, store, sessions_cache, _, amo = build_process_env(session=session)

    def _save_raises(s):
        events.append("save")
        store.saved.append(s)
        raise RuntimeError("disk full")

    store.save = _save_raises  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="disk full"):
        asyncio.run(process_owner_message(**kwargs))

    assert "client_send" in events
    assert "save" in events
    assert "folder" not in events
    assert "amo_pipeline" not in events
    assert sessions_cache == {}
    assert session.history[-1]["role"] == "assistant"
    amo.ensure_pipeline.assert_not_called()


def test_folder_error_prevents_amo():
    """add_to_folder is outside try — failure propagates and blocks amoCRM."""
    session = make_owner_session()
    kwargs, events, store, sessions_cache, _, amo = build_process_env(session=session)

    async def _folder_raises(client, sender, folder):
        events.append("folder")
        raise RuntimeError("folder down")

    kwargs["add_to_folder"] = _folder_raises

    with pytest.raises(RuntimeError, match="folder down"):
        asyncio.run(process_owner_message(**kwargs))

    assert "save" in events
    assert "folder" in events
    assert "amo_pipeline" not in events
    assert sessions_cache[session.chat_id] is session
    amo.ensure_pipeline.assert_not_called()


_OWNER_HANDLER_IMPORT_SCRIPT = """
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

BLOCKED_EXACT = {"agent7.tg_userbot"}
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
    if name == "agent7.sessions" and hasattr(mod, "SessionStore"):
        def _blocked_init(self, *args, **kwargs):
            raise AssertionError("SessionStore instantiated during import")

        mod.SessionStore.__init__ = _blocked_init
    return mod

builtins.__import__ = _import_with_sessionstore_guard

from agent7_envoy.owner_handler import OwnerMessageTemplates, process_owner_message

assert callable(process_owner_message)
assert OwnerMessageTemplates is not None
print("OK")
"""


def test_owner_handler_imports_without_side_effects(tmp_path):
    """owner_handler must import in a clean subprocess without forbidden side effects."""
    src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src)

    result = subprocess.run(
        [sys.executable, "-c", _OWNER_HANDLER_IMPORT_SCRIPT, str(tmp_path)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "OK" in result.stdout
    assert list(tmp_path.iterdir()) == []
