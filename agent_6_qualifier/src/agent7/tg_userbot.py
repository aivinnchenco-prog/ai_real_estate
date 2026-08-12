"""Compatibility shim. Canonical import: ``agent6_qualifier.tg_userbot``.

Legacy launch: ``python3 -m agent7.tg_userbot``
"""
from __future__ import annotations

import asyncio

import agent6_qualifier.tg_userbot as _canonical
from agent6_qualifier.telegram_folders import (
    CLIENTS_FOLDER,
    OWNERS_FOLDER,
)
from agent6_qualifier.tg_userbot import (
    add_to_folder,
    assign_role_folder,
    ensure_amo_lead,
    get_session,
    handle_owner_message,
    humanized_respond,
    load_env,
    main,
    make_client,
    make_script_client,
    run_schema_check,
)

# Re-export types used by runtime tests and scripts
AmoClient = _canonical.AmoClient
Qualifier = _canonical.Qualifier
Session = _canonical.Session
SessionStore = _canonical.SessionStore
asyncio = _canonical.asyncio
events = _canonical.events

_RUNTIME_ALIASES = frozenset({
    "_store",
    "_sessions",
    "_outreach_inflight",
    "brain",
    "notion_store",
})


def __getattr__(name: str):
    if name in _RUNTIME_ALIASES:
        return getattr(_canonical, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CLIENTS_FOLDER",
    "OWNERS_FOLDER",
    "add_to_folder",
    "assign_role_folder",
    "AmoClient",
    "Qualifier",
    "Session",
    "SessionStore",
    "add_to_folder",
    "asyncio",
    "brain",
    "ensure_amo_lead",
    "events",
    "get_session",
    "handle_owner_message",
    "humanized_respond",
    "load_env",
    "main",
    "make_client",
    "make_script_client",
    "notion_store",
    "run_schema_check",
]

if __name__ == "__main__":
    asyncio.run(main())
