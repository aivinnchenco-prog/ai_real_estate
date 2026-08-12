"""Canonical contact role — source of truth for CLIENT / OWNER / AGENT / UNKNOWN.

amoCRM and WhatsApp native lists are mirrors only.
"""

from __future__ import annotations

from .assign import AssignResult, assign_known_role, unlock_manual_override
from .classify import ClassificationResult, classify_unknown_contact_role
from .diagnose import DualSyncDryRunReport, plan_dual_sync_dry_run
from .flags import (
    amo_sync_enabled,
    auto_assign_enabled,
    one_shot_live_test_enabled,
    whatsapp_ui_sync_enabled,
)
from .mapping import (
    AMO_FIELD_NAME,
    AMO_FIELD_VALUES,
    MANAGED_TAGS,
    amo_field_value,
    amo_tag_for_role,
    role_from_notion_owner_agent_type,
    whatsapp_list_for_role,
)
from .one_shot import OneShotLiveGuard, live_auto_assign_gate
from .policy import ROLE_SOURCE_PRIORITY, can_overwrite_role, source_rank
from .roles import CanonicalRole
from .router import RoutingDecision, route_contact
from .routing_targets import RouteTarget, route_for_role
from .sources import RoleSource
from .state import ContactRoleState, ContactRoleStore
from .sync.coordinator import ContactRoleSyncCoordinator, SyncFanOutResult

__all__ = [
    "AMO_FIELD_NAME",
    "AMO_FIELD_VALUES",
    "AssignResult",
    "CanonicalRole",
    "ClassificationResult",
    "ContactRoleState",
    "ContactRoleStore",
    "ContactRoleSyncCoordinator",
    "DualSyncDryRunReport",
    "MANAGED_TAGS",
    "OneShotLiveGuard",
    "ROLE_SOURCE_PRIORITY",
    "RoleSource",
    "RouteTarget",
    "RoutingDecision",
    "SyncFanOutResult",
    "amo_field_value",
    "amo_sync_enabled",
    "amo_tag_for_role",
    "assign_known_role",
    "auto_assign_enabled",
    "can_overwrite_role",
    "classify_unknown_contact_role",
    "live_auto_assign_gate",
    "one_shot_live_test_enabled",
    "plan_dual_sync_dry_run",
    "role_from_notion_owner_agent_type",
    "route_contact",
    "route_for_role",
    "source_rank",
    "unlock_manual_override",
    "whatsapp_list_for_role",
    "whatsapp_ui_sync_enabled",
]
