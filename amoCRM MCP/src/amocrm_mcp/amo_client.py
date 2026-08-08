"""Read-only amoCRM API v4 client."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import requests

from .auth import redact_secrets, require_amo_credentials
from .errors import (
    AmoMcpError,
    AuthError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    ReadOnlyViolation,
)

ALLOWED_METHODS = frozenset({"GET"})


class ReadOnlyAmoClient:
    def __init__(self, subdomain: str, access_token: str, *, timeout: int = 30):
        require_amo_credentials(subdomain, access_token)
        sub = subdomain.replace("https://", "").split(".")[0]
        self.base = f"https://{sub}.amocrm.ru/api/v4"
        self.access_token = access_token
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, *, params: dict | None = None) -> Any:
        method = method.upper()
        if method not in ALLOWED_METHODS:
            raise ReadOnlyViolation(f"Blocked non-GET method: {method}")
        url = f"{self.base}{path}"
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        try:
            response = requests.get(url, headers=self.headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise AmoMcpError(redact_secrets(str(exc), token=self.access_token)) from exc

        if response.status_code == 401:
            raise AuthError("amoCRM authentication failed", status=401)
        if response.status_code == 403:
            raise PermissionDeniedError("amoCRM permission denied", status=403)
        if response.status_code == 404:
            raise NotFoundError("amoCRM resource not found", status=404)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitedError(
                f"amoCRM rate limited; retry_after={retry_after or 'unknown'}",
                status=429,
            )
        if response.status_code >= 400:
            body = redact_secrets((response.text or "")[:500], token=self.access_token)
            raise AmoMcpError(f"amoCRM API error {response.status_code}: {body}", status=response.status_code)

        if not response.text:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise AmoMcpError("amoCRM returned malformed JSON", status=response.status_code) from exc

    # ---------- account / users ----------

    def get_account(self, *, with_params: str = "") -> dict:
        path = "/account"
        if with_params:
            path = f"{path}?with={with_params}"
        return self.request("GET", path) or {}

    def list_users(self, *, page: int = 1, limit: int = 250) -> list[dict]:
        data = self.request("GET", "/users", params={"page": page, "limit": limit})
        return list((data or {}).get("_embedded", {}).get("users", []))

    def get_user(self, user_id: int) -> dict:
        return self.request("GET", f"/users/{int(user_id)}") or {}

    # ---------- pipelines ----------

    def list_pipelines(self) -> list[dict]:
        data = self.request("GET", "/leads/pipelines")
        return list((data or {}).get("_embedded", {}).get("pipelines", []))

    def get_pipeline(self, pipeline_id: int) -> dict:
        return self.request("GET", f"/leads/pipelines/{int(pipeline_id)}") or {}

    # ---------- custom fields ----------

    def list_lead_fields(self) -> list[dict]:
        fields: list[dict] = []
        page = 1
        while True:
            data = self.request("GET", "/leads/custom_fields", params={"page": page, "limit": 50})
            batch = list((data or {}).get("_embedded", {}).get("custom_fields", []))
            fields.extend(batch)
            if not data.get("_links", {}).get("next"):
                break
            page += 1
        return fields

    def list_contact_fields(self) -> list[dict]:
        fields: list[dict] = []
        page = 1
        while True:
            data = self.request("GET", "/contacts/custom_fields", params={"page": page, "limit": 50})
            batch = list((data or {}).get("_embedded", {}).get("custom_fields", []))
            fields.extend(batch)
            if not data.get("_links", {}).get("next"):
                break
            page += 1
        return fields

    # ---------- leads ----------

    def search_leads(
        self,
        *,
        query: str | None = None,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        responsible_user_id: int | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict]:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if query:
            params["query"] = query
        if pipeline_id is not None:
            params["filter[pipeline_id]"] = int(pipeline_id)
        if status_id is not None:
            params["filter[statuses][0][pipeline_id]"] = int(pipeline_id or 0)
            params["filter[statuses][0][status_id]"] = int(status_id)
        if responsible_user_id is not None:
            params["filter[responsible_user_id]"] = int(responsible_user_id)
        data = self.request("GET", "/leads", params=params)
        return list((data or {}).get("_embedded", {}).get("leads", []))

    def get_lead(self, lead_id: int, *, with_entities: str = "contacts,companies,tags") -> dict:
        path = f"/leads/{int(lead_id)}"
        if with_entities:
            path = f"{path}?with={with_entities}"
        return self.request("GET", path) or {}

    def get_lead_links(self, lead_id: int) -> list[dict]:
        data = self.request("GET", f"/leads/{int(lead_id)}/links")
        return list((data or {}).get("_embedded", {}).get("links", []))

    def get_lead_notes(self, lead_id: int, *, page: int = 1, limit: int = 20) -> list[dict]:
        data = self.request(
            "GET",
            f"/leads/{int(lead_id)}/notes",
            params={"page": page, "limit": limit},
        )
        notes = list((data or {}).get("_embedded", {}).get("notes", []))
        if notes:
            return notes
        data = self.request(
            "GET",
            "/leads/notes",
            params={
                "filter[entity_id]": int(lead_id),
                "page": page,
                "limit": limit,
            },
        )
        return list((data or {}).get("_embedded", {}).get("notes", []))

    # ---------- contacts ----------

    def search_contacts(self, *, query: str | None = None, page: int = 1, limit: int = 20) -> list[dict]:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if query:
            params["query"] = query
        data = self.request("GET", "/contacts", params=params)
        return list((data or {}).get("_embedded", {}).get("contacts", []))

    def get_contact(self, contact_id: int, *, with_entities: str = "leads") -> dict:
        path = f"/contacts/{int(contact_id)}"
        if with_entities:
            path = f"{path}?with={with_entities}"
        return self.request("GET", path) or {}

    def get_contact_chats(self, contact_id: int) -> list[dict]:
        data = self.request("GET", "/contacts/chats", params={"contact_id": int(contact_id)})
        return list((data or {}).get("_embedded", {}).get("chats", []))

    # ---------- tasks ----------

    def list_tasks(
        self,
        *,
        entity_id: int | None = None,
        entity_type: str = "leads",
        responsible_user_id: int | None = None,
        is_completed: bool | None = None,
        task_type_id: int | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict]:
        params: dict[str, Any] = {"page": page, "limit": limit}
        if entity_type:
            params["filter[entity_type]"] = entity_type
        if entity_id is not None:
            params["filter[entity_id]"] = int(entity_id)
        if responsible_user_id is not None:
            params["filter[responsible_user_id]"] = int(responsible_user_id)
        if is_completed is not None:
            params["filter[is_completed]"] = 1 if is_completed else 0
        if task_type_id is not None:
            params["filter[task_type_id]"] = int(task_type_id)
        data = self.request("GET", "/tasks", params=params)
        return list((data or {}).get("_embedded", {}).get("tasks", []))

    def get_lead_tasks(self, lead_id: int, *, limit: int = 20) -> list[dict]:
        return self.list_tasks(entity_id=int(lead_id), entity_type="leads", limit=limit)

    def backoff_sleep(self, seconds: float = 1.0) -> None:
        time.sleep(seconds)
