"""Optional WhatsApp Business native-list sync (UI convenience only).

Canonical contact roles live in Agent 6 / Agent 7 / amoCRM.
This package never routes agents and never sends WhatsApp messages.
"""

from .roles import CanonicalRole, map_role_to_list, plan_list_membership
from .results import SyncCode, SyncResult
from .worker import WhatsAppNativeListSyncWorker

__all__ = [
    "CanonicalRole",
    "SyncCode",
    "SyncResult",
    "WhatsAppNativeListSyncWorker",
    "map_role_to_list",
    "plan_list_membership",
]
