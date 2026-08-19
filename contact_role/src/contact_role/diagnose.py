"""Pure dry-run planner for dual sync diagnose (no CRM/browser writes)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .mapping import (
    AMO_FIELD_NAME,
    MANAGED_TAGS,
    amo_field_value,
    amo_tag_for_role,
    whatsapp_list_for_role,
)
from .phone import mask_phone, normalize_phone_e164
from .policy import can_overwrite_role, source_rank
from .roles import CanonicalRole
from .routing_targets import route_for_role
from .sources import RoleSource
from .state import ContactRoleState, ContactRoleStore
from .sync.amocrm import (
    AmoContactClient,
    find_contact_type_field,
    plan_tag_update,
    resolve_contact_by_phone,
)


@dataclass
class DualSyncDryRunReport:
    phone: str
    masked_phone: str
    # Canonical
    old_role: str
    new_role: str
    old_source: str
    new_source: str
    priority_allowed: bool
    priority_reason: str
    route: str
    would_commit_canonical: bool
    # amoCRM
    amo_contact_resolved: str = "SKIPPED"  # YES/NO/AMBIGUOUS/SKIPPED
    amo_contact_id: int | None = None
    amo_current_field: str = ""
    amo_desired_field: str = ""
    amo_current_managed_tags: list[str] = field(default_factory=list)
    amo_desired_managed_tags: list[str] = field(default_factory=list)
    amo_would_write: str = "NO"
    amo_notes: list[str] = field(default_factory=list)
    # WhatsApp
    wa_contact_found: str = "NOT_PROBED"  # YES/NO/NOT_PROBED
    wa_current_lists: list[str] = field(default_factory=list)
    wa_target_list: str | None = None
    wa_would_add: list[str] = field(default_factory=list)
    wa_would_remove: list[str] = field(default_factory=list)
    wa_would_write: str = "NO"
    wa_notes: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [
            "=== contact_role dual-sync DRY-RUN (NO WRITES) ===",
            "",
            "Canonical:",
            f"  phone:           {self.masked_phone}",
            f"  old role:        {self.old_role}",
            f"  new role:        {self.new_role}",
            f"  old source:      {self.old_source}",
            f"  new source:      {self.new_source}",
            f"  priority:        {'ALLOW' if self.priority_allowed else 'BLOCK'} ({self.priority_reason})",
            f"  routing target:  {self.route}",
            f"  would_commit:    {'YES' if self.would_commit_canonical else 'NO'}",
            "",
            "amoCRM:",
            f"  contact resolved: {self.amo_contact_resolved}",
            f"  contact_id:       {self.amo_contact_id or '-'}",
            f"  current field:    {self.amo_current_field or '(empty/unknown)'}",
            f"  desired field:    {self.amo_desired_field}",
            f"  current tags:     {', '.join(self.amo_current_managed_tags) or '(none)'}",
            f"  desired tags:     {', '.join(self.amo_desired_managed_tags) or '(none managed)'}",
            f"  would_write:      {self.amo_would_write}",
        ]
        for n in self.amo_notes:
            lines.append(f"  note: {n}")
        lines += [
            "",
            "WhatsApp UI:",
            f"  contact found:    {self.wa_contact_found}",
            f"  current lists:    {', '.join(self.wa_current_lists) or '(none/unknown)'}",
            f"  target list:      {self.wa_target_list or '(no-op)'}",
            f"  would_add:        {', '.join(self.wa_would_add) or '(none)'}",
            f"  would_remove:     {', '.join(self.wa_would_remove) or '(none)'}",
            f"  would_write:      {self.wa_would_write}",
        ]
        for n in self.wa_notes:
            lines.append(f"  note: {n}")
        lines += [
            "",
            "Safety: CONTACT_ROLE_AMO_DRY_RUN / WHATSAPP_UI_DRY_RUN — no live writes.",
        ]
        return "\n".join(lines)


def plan_dual_sync_dry_run(
    *,
    phone: str,
    role: CanonicalRole | str,
    source: RoleSource | str,
    store: ContactRoleStore | None = None,
    amo: AmoContactClient | None = None,
    wa_current_lists: list[str] | None = None,
    wa_contact_found: str = "NOT_PROBED",
) -> DualSyncDryRunReport:
    """Build dry-run report. Never writes amoCRM or WhatsApp."""
    phone_n = normalize_phone_e164(phone) or phone
    role_n = CanonicalRole.parse(role)
    source_n = RoleSource.parse(source)
    store_u = store or ContactRoleStore()
    current = store_u.get_by_phone(phone_n)

    old_role = (
        CanonicalRole.parse(current.canonical_role)
        if current
        else CanonicalRole.UNKNOWN
    )
    old_source = (
        RoleSource.parse(current.role_source) if current else RoleSource.UNKNOWN
    )
    allowed = can_overwrite_role(
        current, new_role=role_n, new_source=source_n
    )
    if not allowed:
        reason = "priority_blocked"
        if current and current.locked_by_manual_override:
            reason = "manual_lock"
    elif old_role == role_n:
        reason = "same_role_idempotent"
    else:
        reason = (
            f"source_rank {source_rank(source_n)} >= "
            f"current {source_rank(old_source)}"
        )

    would_commit = allowed and old_role != role_n

    report = DualSyncDryRunReport(
        phone=phone_n,
        masked_phone=mask_phone(phone_n),
        old_role=old_role.value,
        new_role=role_n.value,
        old_source=old_source.value,
        new_source=source_n.value,
        priority_allowed=allowed,
        priority_reason=reason,
        route=route_for_role(role_n if would_commit or allowed else old_role).value
        if allowed
        else route_for_role(old_role).value,
        would_commit_canonical=would_commit,
        amo_desired_field=amo_field_value(role_n),
        wa_target_list=whatsapp_list_for_role(role_n),
        wa_contact_found=wa_contact_found,
        amo_would_write="NO",
        wa_would_write="NO",
    )

    # --- amoCRM read-only plan ---
    if role_n is CanonicalRole.UNKNOWN:
        report.amo_desired_managed_tags = []
    else:
        tag = amo_tag_for_role(role_n)
        report.amo_desired_managed_tags = [tag] if tag else []

    if amo is None:
        report.amo_contact_resolved = "SKIPPED"
        report.amo_notes.append("amo client not provided — plan only")
        report.amo_notes.append(
            f"desired field '{AMO_FIELD_NAME}' = {report.amo_desired_field}"
        )
    else:
        code, cid, _ids = resolve_contact_by_phone(amo, phone_n)
        if code == "AMBIGUOUS_CONTACT":
            report.amo_contact_resolved = "AMBIGUOUS"
            report.amo_notes.append("multiple exact phone matches — refuse write")
        elif code != "OK" or cid is None:
            report.amo_contact_resolved = "NO"
            report.amo_notes.append("exact E.164 contact not found")
        else:
            report.amo_contact_resolved = "YES"
            report.amo_contact_id = cid
            contact = amo.get_contact(cid)
            fields = amo.list_contact_custom_fields()
            type_field = find_contact_type_field(fields)
            if type_field is None:
                report.amo_notes.append(
                    f"FIELD_MISSING: create contact field '{AMO_FIELD_NAME}' manually"
                )
            else:
                field_id = int(type_field["id"])
                for cf in contact.get("custom_fields_values") or []:
                    if int(cf.get("field_id") or 0) == field_id:
                        vals = cf.get("values") or []
                        if vals:
                            report.amo_current_field = str(vals[0].get("value") or "")
                        break
            embedded = (contact.get("_embedded") or {}).get("tags") or []
            cur_tags = [
                str(t.get("name") or "")
                for t in embedded
                if isinstance(t, dict) and str(t.get("name") or "") in MANAGED_TAGS
            ]
            report.amo_current_managed_tags = cur_tags
            planned = plan_tag_update(embedded, role_n)
            report.amo_desired_managed_tags = [
                t["name"] for t in planned if t["name"] in MANAGED_TAGS
            ]
            report.amo_notes.append("read-only plan — would_write forced NO")

    # --- WhatsApp plan (membership math only; writes forced NO) ---
    current_lists = list(wa_current_lists or [])
    report.wa_current_lists = current_lists
    target = whatsapp_list_for_role(role_n)
    report.wa_target_list = target
    managed = {"Client", "Owner"}
    cur_managed = [n for n in current_lists if n in managed]
    if target is None:
        report.wa_would_add = []
        report.wa_would_remove = []
        report.wa_notes.append("UNKNOWN → WhatsApp no-op")
    else:
        report.wa_would_add = [] if target in cur_managed else [target]
        report.wa_would_remove = [n for n in cur_managed if n != target]
    if wa_contact_found == "NOT_PROBED":
        report.wa_notes.append(
            "browser not probed — list plan is theoretical from mapping"
        )
    report.wa_notes.append("Playwright write disabled — would_write forced NO")

    return report
