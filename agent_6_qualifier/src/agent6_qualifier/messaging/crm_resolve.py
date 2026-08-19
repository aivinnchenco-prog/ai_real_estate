"""Conservative CRM conversation resolution for Wazzup ↔ amoCRM.

Wazzup already syncs WhatsApp into amoCRM. Agent 6 must not create duplicates
blindly. Exact Wazzup↔amoCRM mapping API is not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from agent6_qualifier.messaging.wazzup_config import normalize_phone_e164_digits


class AmoLookup(Protocol):
    def find_contact(self, query: str) -> dict | None: ...

    def find_open_lead(self, contact_id: int) -> int | None: ...


@dataclass
class CrmConversationResolution:
    found: bool
    contact_id: int | None = None
    lead_id: int | None = None
    phone: str | None = None
    action: str = "none"  # reuse_existing | create_allowed | insufficient_data
    notes: list[str] | None = None

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []


def resolve_existing_crm_conversation(
    amo: AmoLookup | None,
    *,
    phone: str | None,
    chat_id: str | None = None,
) -> CrmConversationResolution:
    """Look up existing amoCRM contact/open lead by phone before create.

    If amo is None or phone missing → insufficient_data (do not create).
    If contact+open lead found → reuse_existing.
    If contact found without open lead → create_allowed (caller may create lead only).
    If nothing found → create_allowed (caller may create contact+lead via existing ensure logic).
    """
    notes: list[str] = [
        "Wazzup↔amoCRM already syncs chats externally — prefer reuse",
        "No assumed Wazzup mapping API; phone lookup only",
    ]
    normalized = normalize_phone_e164_digits(phone or chat_id)
    if amo is None:
        return CrmConversationResolution(
            found=False,
            phone=normalized,
            action="insufficient_data",
            notes=notes + ["amo client unavailable"],
        )
    if not normalized:
        return CrmConversationResolution(
            found=False,
            phone=None,
            action="insufficient_data",
            notes=notes + ["phone missing — skip auto-create"],
        )

    # Try E.164 digits and +prefixed form for amo query flexibility.
    queries = [normalized, f"+{normalized}"]
    contact: dict[str, Any] | None = None
    for q in queries:
        contact = amo.find_contact(q)
        if contact:
            break

    if not contact:
        return CrmConversationResolution(
            found=False,
            phone=normalized,
            action="create_allowed",
            notes=notes + ["no contact found by phone"],
        )

    contact_id = int(contact["id"])
    open_lead = amo.find_open_lead(contact_id)
    if open_lead:
        return CrmConversationResolution(
            found=True,
            contact_id=contact_id,
            lead_id=int(open_lead),
            phone=normalized,
            action="reuse_existing",
            notes=notes + ["open lead reused — do not create duplicate"],
        )
    return CrmConversationResolution(
        found=True,
        contact_id=contact_id,
        lead_id=None,
        phone=normalized,
        action="create_allowed",
        notes=notes + ["contact exists, no open lead — lead create may proceed"],
    )
