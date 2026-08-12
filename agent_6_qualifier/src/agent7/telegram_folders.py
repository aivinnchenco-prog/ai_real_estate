"""Compatibility shim. Canonical import: ``agent6_qualifier.telegram_folders``."""
from agent6_qualifier.telegram_folders import (
    AGENT_FOLDER,
    CLIENT_FOLDER,
    OWNER_FOLDER,
    add_to_folder,
    assign_role_folder,
    remove_from_folder,
    role_folder_title,
)

# Legacy constant names point at new folder titles (not old «Клиенты»/«Собственники»).
CLIENTS_FOLDER = CLIENT_FOLDER
OWNERS_FOLDER = OWNER_FOLDER

__all__ = [
    "AGENT_FOLDER",
    "CLIENT_FOLDER",
    "CLIENTS_FOLDER",
    "OWNER_FOLDER",
    "OWNERS_FOLDER",
    "add_to_folder",
    "assign_role_folder",
    "remove_from_folder",
    "role_folder_title",
]
