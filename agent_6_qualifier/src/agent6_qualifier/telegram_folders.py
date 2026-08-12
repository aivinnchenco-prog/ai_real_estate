"""Telegram dialog-folder integration (shared runtime helper).

Official corporate account folders (exact titles, created manually in TG):
  - «Клиент» — client dialogs (Agent 6)
  - «Owner» — listing role Владелец (Agent 7)
  - «Agent» — listing role Агент (Agent 7)

Folders are UI convenience only; routing uses owner_registry / sessions, not folders.
"""
from __future__ import annotations

from telethon import TelegramClient, functions, types

from .alerts import notify_error

CLIENT_FOLDER = "Клиент"
OWNER_FOLDER = "Owner"
AGENT_FOLDER = "Agent"

ROLE_FOLDERS = frozenset({OWNER_FOLDER, AGENT_FOLDER})

# Legacy names — do not use for new code (kept so imports fail loudly in tests).
CLIENTS_FOLDER = CLIENT_FOLDER
OWNERS_FOLDER = OWNER_FOLDER

_NOTION_OWNER = "Владелец"
_NOTION_AGENT = "Агент"


def role_folder_title(owner_agent_type: str) -> str | None:
    """Map Notion «Агент/Владелец (тип)» → Telegram folder title."""
    value = (owner_agent_type or "").strip()
    if value == _NOTION_OWNER:
        return OWNER_FOLDER
    if value == _NOTION_AGENT:
        return AGENT_FOLDER
    return None


def _folder_title(filter_obj: types.DialogFilter) -> str:
    return getattr(filter_obj.title, "text", filter_obj.title)


async def _list_dialog_filters(client: TelegramClient) -> list[types.DialogFilter]:
    result = await client(functions.messages.GetDialogFiltersRequest())
    return [f for f in result.filters if isinstance(f, types.DialogFilter)]


async def _find_folder(client: TelegramClient, folder_title: str) -> types.DialogFilter | None:
    filters = await _list_dialog_filters(client)
    return next(
        (f for f in filters if _folder_title(f) == folder_title),
        None,
    )


async def _persist_filter(client: TelegramClient, target: types.DialogFilter) -> None:
    await client(functions.messages.UpdateDialogFilterRequest(
        id=target.id, filter=target,
    ))


async def add_to_folder(client: TelegramClient, entity, folder_title: str) -> None:
    """Place dialog into an existing folder. Does not auto-create folders."""
    try:
        target = await _find_folder(client, folder_title)
        if target is None:
            notify_error(
                "tg.folders",
                "folder not found",
                f"папка «{folder_title}» — создайте вручную в Telegram",
            )
            return
        peer = await client.get_input_entity(entity)
        if any(p == peer for p in target.include_peers):
            return
        target.include_peers.append(peer)
        await _persist_filter(client, target)
    except Exception as e:
        notify_error("tg.folders", str(e), f"папка «{folder_title}»")


async def remove_from_folder(client: TelegramClient, entity, folder_title: str) -> None:
    """Remove dialog from folder if present. Missing folder is a no-op."""
    try:
        target = await _find_folder(client, folder_title)
        if target is None:
            return
        peer = await client.get_input_entity(entity)
        if not any(p == peer for p in target.include_peers):
            return
        target.include_peers = [p for p in target.include_peers if p != peer]
        await _persist_filter(client, target)
    except Exception as e:
        notify_error("tg.folders", str(e), f"снять с папки «{folder_title}»")


async def assign_role_folder(
    client: TelegramClient, entity, owner_agent_type: str,
) -> None:
    """Assign Owner/Agent folder from Notion role; correct if role changed."""
    target = role_folder_title(owner_agent_type)
    if not target:
        return
    for folder in ROLE_FOLDERS - {target}:
        await remove_from_folder(client, entity, folder)
    await add_to_folder(client, entity, target)
