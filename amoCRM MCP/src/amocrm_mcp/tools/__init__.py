"""MCP tool implementations (read-only)."""

from __future__ import annotations

from typing import Any

from ..amo_client import ReadOnlyAmoClient
from ..cache import TtlCache
from ..config import Settings
from ..errors import InvalidArgumentError
from ..normalize import (
    normalize_account,
    normalize_chat_binding,
    normalize_contact_summary,
    normalize_custom_field,
    normalize_lead_detail,
    normalize_lead_summary,
    normalize_link,
    normalize_note,
    normalize_pipeline,
    normalize_task,
    normalize_user,
    wrap_data,
)


def _bound_limit(value: int | None, settings: Settings) -> int:
    limit = settings.default_limit if value is None else int(value)
    if limit < 1:
        raise InvalidArgumentError("limit must be >= 1")
    return min(limit, settings.max_limit)


class AmoTools:
    def __init__(self, client: ReadOnlyAmoClient, settings: Settings):
        self.client = client
        self.settings = settings
        self.cache = TtlCache(settings.cache_ttl_seconds)

    def amo_get_account(self, *, include_raw: bool = False) -> dict:
        raw = self.client.get_account(with_params="task_types")
        return wrap_data(normalize_account(raw), include_raw=include_raw, raw=raw if include_raw else None)

    def amo_list_users(self, *, limit: int | None = None, include_raw: bool = False) -> dict:
        limit = _bound_limit(limit, self.settings)
        users = self.cache.get_or_set(
            f"users:{limit}",
            lambda: self.client.list_users(limit=limit),
        )
        payload = [normalize_user(u) for u in users]
        return wrap_data(payload, include_raw=include_raw, raw=users if include_raw else None)

    def amo_get_user(self, user_id: int, *, include_raw: bool = False) -> dict:
        raw = self.client.get_user(user_id)
        return wrap_data(normalize_user(raw), include_raw=include_raw, raw=raw if include_raw else None)

    def amo_list_pipelines(self, *, include_raw: bool = False) -> dict:
        pipelines = self.cache.get_or_set("pipelines", self.client.list_pipelines)
        payload = [normalize_pipeline(p) for p in pipelines]
        return wrap_data(payload, include_raw=include_raw, raw=pipelines if include_raw else None)

    def amo_get_pipeline(self, pipeline_id: int, *, include_raw: bool = False) -> dict:
        raw = self.client.get_pipeline(pipeline_id)
        return wrap_data(normalize_pipeline(raw), include_raw=include_raw, raw=raw if include_raw else None)

    def amo_list_lead_fields(self, *, include_raw: bool = False) -> dict:
        fields = self.cache.get_or_set("lead_fields", self.client.list_lead_fields)
        payload = [normalize_custom_field(f) for f in fields]
        return wrap_data(payload, include_raw=include_raw, raw=fields if include_raw else None)

    def amo_list_contact_fields(self, *, include_raw: bool = False) -> dict:
        fields = self.cache.get_or_set("contact_fields", self.client.list_contact_fields)
        payload = [normalize_custom_field(f) for f in fields]
        return wrap_data(payload, include_raw=include_raw, raw=fields if include_raw else None)

    def amo_search_leads(
        self,
        *,
        query: str | None = None,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        responsible_user_id: int | None = None,
        page: int = 1,
        limit: int | None = None,
        include_raw: bool = False,
    ) -> dict:
        limit = _bound_limit(limit, self.settings)
        leads = self.client.search_leads(
            query=query,
            pipeline_id=pipeline_id,
            status_id=status_id,
            responsible_user_id=responsible_user_id,
            page=page,
            limit=limit,
        )
        payload = [normalize_lead_summary(l) for l in leads]
        return wrap_data({"items": payload, "page": page, "limit": limit}, include_raw=include_raw, raw=leads if include_raw else None)

    def amo_get_lead(self, lead_id: int, *, include_raw: bool = False) -> dict:
        raw = self.client.get_lead(lead_id)
        return wrap_data(normalize_lead_detail(raw), include_raw=include_raw, raw=raw if include_raw else None)

    def amo_get_lead_links(self, lead_id: int, *, include_raw: bool = False) -> dict:
        links = self.client.get_lead_links(lead_id)
        payload = [normalize_link(l) for l in links]
        return wrap_data(payload, include_raw=include_raw, raw=links if include_raw else None)

    def amo_search_contacts(
        self,
        *,
        query: str | None = None,
        page: int = 1,
        limit: int | None = None,
        include_raw: bool = False,
    ) -> dict:
        limit = _bound_limit(limit, self.settings)
        contacts = self.client.search_contacts(query=query, page=page, limit=limit)
        payload = [normalize_contact_summary(c) for c in contacts]
        return wrap_data({"items": payload, "page": page, "limit": limit}, include_raw=include_raw, raw=contacts if include_raw else None)

    def amo_get_contact(self, contact_id: int, *, include_raw: bool = False) -> dict:
        raw = self.client.get_contact(contact_id)
        return wrap_data(normalize_contact_summary(raw), include_raw=include_raw, raw=raw if include_raw else None)

    def amo_get_contact_chats(self, contact_id: int, *, include_raw: bool = False) -> dict:
        chats = self.client.get_contact_chats(contact_id)
        payload = [normalize_chat_binding(c) for c in chats]
        return wrap_data(payload, include_raw=include_raw, raw=chats if include_raw else None)

    def amo_list_tasks(
        self,
        *,
        entity_id: int | None = None,
        responsible_user_id: int | None = None,
        is_completed: bool | None = None,
        task_type: int | None = None,
        page: int = 1,
        limit: int | None = None,
        include_raw: bool = False,
    ) -> dict:
        limit = _bound_limit(limit, self.settings)
        tasks = self.client.list_tasks(
            entity_id=entity_id,
            responsible_user_id=responsible_user_id,
            is_completed=is_completed,
            task_type_id=task_type,
            page=page,
            limit=limit,
        )
        payload = [normalize_task(t) for t in tasks]
        return wrap_data({"items": payload, "page": page, "limit": limit}, include_raw=include_raw, raw=tasks if include_raw else None)

    def amo_get_lead_tasks(self, lead_id: int, *, limit: int | None = None, include_raw: bool = False) -> dict:
        limit = _bound_limit(limit, self.settings)
        tasks = self.client.get_lead_tasks(lead_id, limit=limit)
        payload = [normalize_task(t) for t in tasks]
        return wrap_data(payload, include_raw=include_raw, raw=tasks if include_raw else None)

    def amo_get_lead_notes(
        self,
        lead_id: int,
        *,
        page: int = 1,
        limit: int | None = None,
        include_raw: bool = False,
    ) -> dict:
        limit = _bound_limit(limit, self.settings)
        notes = self.client.get_lead_notes(lead_id, page=page, limit=limit)
        payload = [normalize_note(n) for n in notes]
        return wrap_data({"items": payload, "page": page, "limit": limit}, include_raw=include_raw, raw=notes if include_raw else None)

    def amo_get_deal_overview(self, lead_id: int, *, notes_limit: int = 10, include_raw: bool = False) -> dict:
        notes_limit = min(_bound_limit(notes_limit, self.settings), 20)
        lead = self.client.get_lead(lead_id)
        links = self.client.get_lead_links(lead_id)
        tasks = self.client.get_lead_tasks(lead_id, limit=20)
        notes = self.client.get_lead_notes(lead_id, limit=notes_limit)
        contact_ids = [c.get("id") for c in (lead.get("_embedded") or {}).get("contacts", []) if c.get("id")]
        chats = []
        for cid in contact_ids[:5]:
            chats.extend(self.client.get_contact_chats(int(cid)))
        overview = {
            "lead": normalize_lead_detail(lead),
            "links": [normalize_link(l) for l in links],
            "tasks": [normalize_task(t) for t in tasks],
            "notes": [normalize_note(n) for n in notes],
            "chat_bindings": [normalize_chat_binding(c) for c in chats],
        }
        raw = {"lead": lead, "links": links, "tasks": tasks, "notes": notes, "chats": chats} if include_raw else None
        return wrap_data(overview, include_raw=include_raw, raw=raw)
