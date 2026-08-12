"""Unit tests for agent6_qualifier.telegram_folders (offline, mocked Telethon)."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from telethon import functions, types

sys.path.insert(0, str(__file__).rsplit("/tests/", 1)[0] + "/src")

from agent6_qualifier.telegram_folders import (
    AGENT_FOLDER,
    CLIENT_FOLDER,
    OWNER_FOLDER,
    add_to_folder,
    assign_role_folder,
    remove_from_folder,
    role_folder_title,
)


class _FiltersResult:
    def __init__(self, filters):
        self.filters = filters


def _dialog_filter(
    *,
    filter_id: int,
    title: str,
    include_peers=None,
    exclude_peers=None,
    pinned_peers=None,
) -> types.DialogFilter:
    return types.DialogFilter(
        id=filter_id,
        title=types.TextWithEntities(text=title, entities=[]),
        pinned_peers=pinned_peers or [],
        include_peers=list(include_peers or []),
        exclude_peers=exclude_peers or [],
    )


def _build_client(
    filters,
    *,
    peer,
    get_entity_raises: Exception | None = None,
    update_raises: Exception | None = None,
):
    update_calls: list = []

    async def _invoke(request):
        if isinstance(request, functions.messages.GetDialogFiltersRequest):
            return _FiltersResult(filters)
        if isinstance(request, functions.messages.UpdateDialogFilterRequest):
            update_calls.append(request)
            if update_raises:
                raise update_raises
            return None
        raise AssertionError(f"unexpected request: {request!r}")

    client = AsyncMock(side_effect=_invoke)
    if get_entity_raises:
        client.get_input_entity = AsyncMock(side_effect=get_entity_raises)
    else:
        client.get_input_entity = AsyncMock(return_value=peer)
    return client, update_calls


@pytest.fixture
def notify_errors(monkeypatch):
    errors: list[tuple] = []

    def _notify(component, error, context=""):
        errors.append((component, error, context))

    monkeypatch.setattr("agent6_qualifier.telegram_folders.notify_error", _notify)
    return errors


def test_folder_constants_exact_titles():
    assert CLIENT_FOLDER == "Клиент"
    assert OWNER_FOLDER == "Owner"
    assert AGENT_FOLDER == "Agent"
    assert CLIENT_FOLDER != "Клиенты"
    assert OWNER_FOLDER != "Собственники"


def test_role_folder_title_mapping():
    assert role_folder_title("Владелец") == OWNER_FOLDER
    assert role_folder_title("Агент") == AGENT_FOLDER
    assert role_folder_title("") is None
    assert role_folder_title("unknown") is None


def test_existing_folder_appends_peer_and_updates(notify_errors):
    existing_peer = object()
    new_peer = object()
    folder = _dialog_filter(
        filter_id=7,
        title=CLIENT_FOLDER,
        include_peers=[existing_peer],
    )
    client, update_calls = _build_client([folder], peer=new_peer)

    asyncio.run(add_to_folder(client, "entity", CLIENT_FOLDER))

    assert len(update_calls) == 1
    updated = update_calls[0].filter
    assert updated.include_peers == [existing_peer, new_peer]
    assert notify_errors == []


def test_existing_peer_skips_update(notify_errors):
    peer = object()
    folder = _dialog_filter(filter_id=3, title=OWNER_FOLDER, include_peers=[peer])
    client, update_calls = _build_client([folder], peer=peer)

    asyncio.run(add_to_folder(client, "entity", OWNER_FOLDER))

    assert update_calls == []
    assert notify_errors == []


def test_missing_folder_notifies_without_create(notify_errors):
    peer = object()
    other = _dialog_filter(filter_id=5, title="Другое", include_peers=[])
    client, update_calls = _build_client([other], peer=peer)

    asyncio.run(add_to_folder(client, "entity", CLIENT_FOLDER))

    assert update_calls == []
    assert len(notify_errors) == 1
    assert notify_errors[0][0] == "tg.folders"
    assert "not found" in notify_errors[0][1]


def test_get_input_entity_error_notifies_and_swallows(notify_errors):
    folder = _dialog_filter(filter_id=1, title=CLIENT_FOLDER, include_peers=[])
    client, update_calls = _build_client(
        [folder], peer=object(), get_entity_raises=RuntimeError("entity down"),
    )

    asyncio.run(add_to_folder(client, "entity", CLIENT_FOLDER))

    assert update_calls == []
    assert notify_errors[0][1] == "entity down"


def test_assign_role_owner(notify_errors):
    peer = object()
    owner_f = _dialog_filter(filter_id=1, title=OWNER_FOLDER, include_peers=[])
    agent_f = _dialog_filter(filter_id=2, title=AGENT_FOLDER, include_peers=[])
    client, update_calls = _build_client([owner_f, agent_f], peer=peer)

    asyncio.run(assign_role_folder(client, "entity", "Владелец"))

    assert len(update_calls) == 1
    assert update_calls[0].filter.title.text == OWNER_FOLDER
    assert update_calls[0].filter.include_peers == [peer]


def test_assign_role_agent(notify_errors):
    peer = object()
    owner_f = _dialog_filter(filter_id=1, title=OWNER_FOLDER, include_peers=[])
    agent_f = _dialog_filter(filter_id=2, title=AGENT_FOLDER, include_peers=[])
    client, update_calls = _build_client([owner_f, agent_f], peer=peer)

    asyncio.run(assign_role_folder(client, "entity", "Агент"))

    assert len(update_calls) == 1
    assert update_calls[0].filter.title.text == AGENT_FOLDER


def test_unknown_role_no_folder_assignment(notify_errors):
    peer = object()
    owner_f = _dialog_filter(filter_id=1, title=OWNER_FOLDER, include_peers=[])
    client, update_calls = _build_client([owner_f], peer=peer)

    asyncio.run(assign_role_folder(client, "entity", ""))

    assert update_calls == []
    assert notify_errors == []


def test_role_correction_owner_to_agent(notify_errors):
    peer = object()
    owner_f = _dialog_filter(filter_id=1, title=OWNER_FOLDER, include_peers=[peer])
    agent_f = _dialog_filter(filter_id=2, title=AGENT_FOLDER, include_peers=[])
    client, update_calls = _build_client([owner_f, agent_f], peer=peer)

    asyncio.run(assign_role_folder(client, "entity", "Агент"))

    assert len(update_calls) == 2
    titles = [c.filter.title.text for c in update_calls]
    assert OWNER_FOLDER in titles
    assert AGENT_FOLDER in titles
    owner_update = next(c for c in update_calls if c.filter.title.text == OWNER_FOLDER)
    agent_update = next(c for c in update_calls if c.filter.title.text == AGENT_FOLDER)
    assert owner_update.filter.include_peers == []
    assert agent_update.filter.include_peers == [peer]


def test_role_correction_agent_to_owner(notify_errors):
    peer = object()
    owner_f = _dialog_filter(filter_id=1, title=OWNER_FOLDER, include_peers=[])
    agent_f = _dialog_filter(filter_id=2, title=AGENT_FOLDER, include_peers=[peer])
    client, update_calls = _build_client([owner_f, agent_f], peer=peer)

    asyncio.run(assign_role_folder(client, "entity", "Владелец"))

    assert len(update_calls) == 2
    owner_update = next(c for c in update_calls if c.filter.title.text == OWNER_FOLDER)
    agent_update = next(c for c in update_calls if c.filter.title.text == AGENT_FOLDER)
    assert owner_update.filter.include_peers == [peer]
    assert agent_update.filter.include_peers == []


def test_remove_from_folder_idempotent(notify_errors):
    peer = object()
    folder = _dialog_filter(filter_id=1, title=OWNER_FOLDER, include_peers=[])
    client, update_calls = _build_client([folder], peer=peer)

    asyncio.run(remove_from_folder(client, "entity", OWNER_FOLDER))

    assert update_calls == []


_TELEGRAM_FOLDERS_IMPORT_SCRIPT = """
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

BLOCKED_EXACT = {"agent6_qualifier.tg_userbot", "agent7_envoy.auto"}
BLOCKED_PREFIXES = ("agent7_envoy",)

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

from agent6_qualifier.telegram_folders import CLIENT_FOLDER, OWNER_FOLDER, add_to_folder

assert CLIENT_FOLDER == "Клиент"
assert OWNER_FOLDER == "Owner"
assert CLIENT_FOLDER != "Клиенты"
assert OWNER_FOLDER != "Собственники"
assert callable(add_to_folder)
print("OK")
"""


def test_telegram_folders_imports_without_side_effects(tmp_path):
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath(src)

    result = subprocess.run(
        [sys.executable, "-c", _TELEGRAM_FOLDERS_IMPORT_SCRIPT, str(tmp_path)],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "OK" in result.stdout
    assert list(tmp_path.iterdir()) == []
