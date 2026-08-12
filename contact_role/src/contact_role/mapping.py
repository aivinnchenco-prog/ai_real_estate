"""Mappings: Notion / amoCRM / WhatsApp mirrors of canonical role."""

from __future__ import annotations

from .roles import CanonicalRole

# Desired amoCRM contact custom field (create manually if missing).
AMO_FIELD_NAME = "Тип контакта"
AMO_FIELD_VALUES = {
    CanonicalRole.CLIENT: "Клиент",
    CanonicalRole.OWNER: "Владелец",
    CanonicalRole.AGENT: "Агент",
    CanonicalRole.UNKNOWN: "Не определено",
}

MANAGED_TAGS = frozenset({"CLIENT", "OWNER", "AGENT"})

WHATSAPP_LIST_BY_ROLE = {
    CanonicalRole.CLIENT: "Client",
    CanonicalRole.OWNER: "Owner",
    CanonicalRole.AGENT: "Owner",  # no Agent list yet
    CanonicalRole.UNKNOWN: None,
}


def amo_field_value(role: CanonicalRole | str) -> str:
    return AMO_FIELD_VALUES[CanonicalRole.parse(role)]


def amo_tag_for_role(role: CanonicalRole | str) -> str | None:
    r = CanonicalRole.parse(role)
    if r is CanonicalRole.UNKNOWN:
        return None
    return r.value


def whatsapp_list_for_role(role: CanonicalRole | str) -> str | None:
    return WHATSAPP_LIST_BY_ROLE[CanonicalRole.parse(role)]


def role_from_notion_owner_agent_type(value: str | None) -> CanonicalRole:
    """Map Notion «Агент/Владелец (тип)» → canonical role.

    Empty / blank is UNKNOWN — never silently OWNER.
    """
    text = (value or "").strip()
    if text == "Владелец":
        return CanonicalRole.OWNER
    if text == "Агент":
        return CanonicalRole.AGENT
    return CanonicalRole.UNKNOWN


def role_from_amo_field_value(value: str | None) -> CanonicalRole:
    text = (value or "").strip()
    inverse = {v: k for k, v in AMO_FIELD_VALUES.items()}
    return inverse.get(text, CanonicalRole.UNKNOWN)
