"""Telegram dialog-folder integration (shared runtime helper)."""
from __future__ import annotations

from telethon import TelegramClient, functions, types

from .alerts import notify_error

CLIENTS_FOLDER = "Клиенты"
OWNERS_FOLDER = "Собственники"


async def add_to_folder(client: TelegramClient, entity, folder_title: str) -> None:
    """Кладёт диалог в папку (dialog filter). Папка создаётся, если её нет."""
    try:
        result = await client(functions.messages.GetDialogFiltersRequest())
        filters = [f for f in result.filters
                   if isinstance(f, types.DialogFilter)]
        target = next(
            (f for f in filters if getattr(f.title, "text", f.title) == folder_title),
            None,
        )
        peer = await client.get_input_entity(entity)
        if target is None:
            used_ids = [f.id for f in filters]
            new_id = max(used_ids, default=1) + 1
            target = types.DialogFilter(
                id=new_id,
                title=types.TextWithEntities(text=folder_title, entities=[]),
                pinned_peers=[], include_peers=[peer], exclude_peers=[],
            )
        else:
            if any(p == peer for p in target.include_peers):
                return
            target.include_peers.append(peer)
        await client(functions.messages.UpdateDialogFilterRequest(
            id=target.id, filter=target))
    except Exception as e:
        notify_error("tg.folders", str(e), f"папка «{folder_title}»")
