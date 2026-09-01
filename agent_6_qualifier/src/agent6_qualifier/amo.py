"""amoCRM: воронка «Аренда — лиды», поля сделки, создание/обновление лида.

Авторизация — долгосрочный токен (Bearer JWT).
Разметка «никого не терять»: тег OBJ_{object_id} на сделке,
примечания с префиксом [OBJ:...][CLIENT] / [OBJ:...][OWNER].
"""
from __future__ import annotations

import os
from typing import Any

import requests

from .models import LeadProfile

# Стадии воронки (AGENT_SPEC.md, раздел 8). Успешно/Отказ — системные (142/143).
PIPELINE_NAME = "Аренда — лиды"
STAGES = [
    "Новый лид",
    "Квалификация",
    "Подбор",
    "Запрос владельцу",
    "Согласование условий",
    "Бронь подтверждена",
]

# Кастомные поля сделки: имя -> тип amo
LEAD_FIELDS = {
    "Объект ID": "text",
    "Дата заезда": "date",
    "Дата выезда": "date",
    "Бюджет (мес)": "numeric",
    "Допуск по бюджету %": "numeric",
    "Район": "text",
    "Гостей": "numeric",
    "Животные": "select",
    "Канал источника": "text",
    "Альтернативные объекты": "text",
    "Ссылка Notion": "url",
    "Ссылка TG-пост": "url",
    "WhatsApp": "text",
    # Клиент не назвал дату выезда = аренда на год (см. анкету квалификатора)
    "Контракт на год": "checkbox",
}
PETS_OPTIONS = ["да", "нет", "не указано"]


class AmoClient:
    def __init__(self) -> None:
        sub = os.environ["AMO_SUBDOMAIN"].replace("https://", "").split(".")[0]
        self.base = f"https://{sub}.amocrm.ru/api/v4"
        self.headers = {
            "Authorization": f"Bearer {os.environ['AMO_ACCESS_TOKEN']}",
            "Content-Type": "application/json",
        }

    def _req(self, method: str, path: str, **kw) -> Any:
        r = requests.request(method, f"{self.base}{path}", headers=self.headers, timeout=30, **kw)
        r.raise_for_status()
        return r.json() if r.text else {}

    # ---------- одноразовая настройка ----------

    def ensure_pipeline(self) -> dict[str, int]:
        """Создаёт воронку и стадии, если их нет. Возвращает {имя стадии: status_id}."""
        pipes = self._req("GET", "/leads/pipelines")["_embedded"]["pipelines"]
        pipe = next((p for p in pipes if p["name"] == PIPELINE_NAME), None)
        if pipe is None:
            body = [{
                "name": PIPELINE_NAME,
                "is_main": False,
                "is_unsorted_on": False,
                "sort": 100,
                "_embedded": {"statuses": [
                    {"name": name, "sort": (i + 1) * 10} for i, name in enumerate(STAGES)
                ]},
            }]
            pipe = self._req("POST", "/leads/pipelines", json=body)["_embedded"]["pipelines"][0]
        statuses = self._req("GET", f"/leads/pipelines/{pipe['id']}")["_embedded"]["statuses"]
        self.pipeline_id = pipe["id"]
        return {s["name"]: s["id"] for s in statuses}

    def ensure_lead_fields(self) -> dict[str, int]:
        """Создаёт кастомные поля сделки, если их нет. Возвращает {имя: field_id}."""
        if getattr(self, "_field_ids", None):
            return self._field_ids
        existing: dict[str, int] = {}
        page = 1
        while True:
            data = self._req("GET", f"/leads/custom_fields?limit=50&page={page}")
            for f in data.get("_embedded", {}).get("custom_fields", []):
                existing[f["name"]] = f["id"]
            if not data.get("_links", {}).get("next"):
                break
            page += 1

        to_create = []
        for name, ftype in LEAD_FIELDS.items():
            if name in existing:
                continue
            field: dict = {"name": name, "type": ftype}
            if ftype == "select":
                field["enums"] = [{"value": v, "sort": i * 10} for i, v in enumerate(PETS_OPTIONS)]
            to_create.append(field)
        if to_create:
            created = self._req("POST", "/leads/custom_fields", json=to_create)
            for f in created["_embedded"]["custom_fields"]:
                existing[f["name"]] = f["id"]
        self._field_ids = existing
        return existing

    # ---------- рабочие операции ----------

    def find_contact(self, query: str) -> dict | None:
        """Поиск контакта по телефону / username для дедупа лидов."""
        items = self.find_contacts(query)
        return items[0] if items else None

    def find_contacts(self, query: str) -> list[dict]:
        """GET /contacts?query=... — все совпадения (для exact phone resolve)."""
        data = self._req("GET", f"/contacts?query={query}")
        return list((data or {}).get("_embedded", {}).get("contacts", []) or [])

    def find_open_lead(self, contact_id: int) -> int | None:
        """Открытая сделка контакта в любой воронке (Wazzup «Воронка» тоже).

        Открытая = не в системных стадиях «Успешно» (142) / «Закрыто» (143).
        При нескольких открытых берём самую свежую по updated_at/created_at.
        """
        data = self._req("GET", f"/contacts/{contact_id}?with=leads")
        lead_ids = [l["id"] for l in (data or {}).get("_embedded", {}).get("leads", [])]
        open_leads: list[dict] = []
        for lid in lead_ids:
            lead = self._req("GET", f"/leads/{lid}")
            if lead.get("status_id") not in (142, 143):
                open_leads.append(lead)
        if not open_leads:
            return None
        open_leads.sort(
            key=lambda item: item.get("updated_at") or item.get("created_at") or 0,
            reverse=True,
        )
        chosen = open_leads[0]
        print(
            f"[amo] bind lead #{chosen.get('id')} "
            f"pipeline={chosen.get('pipeline_id')} status={chosen.get('status_id')}",
            flush=True,
        )
        return int(chosen["id"])

    def create_contact(self, name: str, phone: str = "", tg_username: str = "") -> int:
        cf = []
        if phone:
            cf.append({"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]})
        body = [{"name": name or "Клиент", "custom_fields_values": cf or None}]
        data = self._req("POST", "/contacts", json=body)
        cid = data["_embedded"]["contacts"][0]["id"]
        if tg_username:
            self.add_note("contacts", cid, f"Telegram: @{tg_username.lstrip('@')}")
        return cid

    def create_lead(
        self,
        lead: LeadProfile,
        contact_id: int,
        status_id: int,
        field_ids: dict[str, int],
        notion_url: str = "",
        tg_post_url: str = "",
    ) -> int:
        def fv(name: str, value) -> dict:
            return {"field_id": field_ids[name], "values": [{"value": value}]}

        cf = [fv("Канал источника", lead.source_channel or "telegram")]
        if lead.preferred_object_id:
            cf.append(fv("Объект ID", lead.preferred_object_id))
        if lead.budget is not None:
            cf.append(fv("Бюджет (мес)", int(lead.budget)))
            cf.append(fv("Допуск по бюджету %", int(lead.budget_tolerance_pct)))
        if lead.districts:
            cf.append(fv("Район", ", ".join(lead.districts)))
        if lead.guests:
            cf.append(fv("Гостей", lead.guests))
        if notion_url:
            cf.append(fv("Ссылка Notion", notion_url))
        if tg_post_url:
            cf.append(fv("Ссылка TG-пост", tg_post_url))

        tags = []
        if lead.preferred_object_id:
            tags.append({"name": f"OBJ_{lead.preferred_object_id}"})

        body = [{
            "name": f"Аренда: {lead.name or 'клиент'}"
                    + (f" / {lead.preferred_object_id}" if lead.preferred_object_id else ""),
            "pipeline_id": self.pipeline_id,
            "status_id": status_id,
            "custom_fields_values": cf,
            "_embedded": {
                "contacts": [{"id": contact_id}],
                "tags": tags or None,
            },
        }]
        return self._req("POST", "/leads", json=body)["_embedded"]["leads"][0]["id"]

    def update_lead_status(self, lead_id: int, status_id: int) -> None:
        self._req("PATCH", f"/leads/{lead_id}", json={"status_id": status_id})

    def build_lead_fields_payload(
        self, lead: LeadProfile, field_ids: dict[str, int],
    ) -> dict:
        """PATCH body for a qualification update. Never writes price:0."""
        from datetime import datetime, time

        def fv(name: str, value) -> dict:
            return {"field_id": field_ids[name], "values": [{"value": value}]}

        def ts(d) -> int:  # amo date-поля принимают unix timestamp
            return int(datetime.combine(d, time(12, 0)).timestamp())

        cf = []
        if lead.preferred_object_id and "Объект ID" in field_ids:
            cf.append(fv("Объект ID", lead.preferred_object_id))
        if lead.check_in and "Дата заезда" in field_ids:
            cf.append(fv("Дата заезда", ts(lead.check_in)))
            # Дата выезда не названа = годовой контракт (анкета квалификатора).
            # Пишем и снятие галочки: клиент мог назвать выезд позже.
            if "Контракт на год" in field_ids:
                cf.append(fv("Контракт на год", not lead.check_out))
        if lead.check_out and "Дата выезда" in field_ids:
            cf.append(fv("Дата выезда", ts(lead.check_out)))
        if lead.budget is not None and "Бюджет (мес)" in field_ids:
            cf.append(fv("Бюджет (мес)", int(lead.budget)))
            if "Допуск по бюджету %" in field_ids:
                cf.append(fv("Допуск по бюджету %", int(lead.budget_tolerance_pct)))
        if lead.districts and "Район" in field_ids:
            cf.append(fv("Район", ", ".join(lead.districts)))
        if lead.guests and "Гостей" in field_ids:
            cf.append(fv("Гостей", lead.guests))
        if lead.whatsapp and "WhatsApp" in field_ids:
            cf.append(fv("WhatsApp", lead.whatsapp))
        if lead.pets is not None and "Животные" in field_ids:
            # Select enums in LEAD_FIELDS / PETS_OPTIONS: да / нет / не указано
            cf.append(fv("Животные", "да" if lead.pets else "нет"))
        if not cf:
            return {}
        body: dict = {"custom_fields_values": cf}
        if lead.budget:
            body["price"] = int(lead.budget)
        return body

    def update_lead_fields(self, lead_id: int, lead: LeadProfile,
                           field_ids: dict[str, int]) -> None:
        """Дозаполняет карточку сделки фактами из профиля лида.

        Сделка создаётся при первом сообщении, когда известен только объект;
        даты/гости/бюджет/район появляются позже — синхронизируем их сюда.
        """
        body = self.build_lead_fields_payload(lead, field_ids)
        if not body:
            return
        self._req("PATCH", f"/leads/{lead_id}", json=body)

    def attach_file(self, lead_id: int, path) -> None:
        """Загружает файл в amo-диск и прикрепляет к сделке (договор брони).

        Схема amo v4: account.drive_url -> сессия загрузки -> байты ->
        file uuid -> PUT /leads/{id}/files.
        """
        from pathlib import Path as _P

        p = _P(path)
        data = p.read_bytes()
        drive = self._req("GET", "/account?with=drive_url").get("drive_url", "")
        if not drive:
            raise RuntimeError("amo: drive_url недоступен для аккаунта")

        content_type = ("application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document")
        session = requests.post(
            f"{drive}/v1.0/sessions",
            headers=self.headers,
            json={"file_name": p.name, "file_size": len(data),
                  "content_type": content_type},
            timeout=30,
        )
        session.raise_for_status()
        upload_url = session.json()["upload_url"]

        up = requests.post(
            upload_url,
            headers={"Authorization": self.headers["Authorization"],
                     "Content-Type": content_type},
            data=data,
            timeout=60,
        )
        up.raise_for_status()
        file_uuid = up.json().get("uuid")
        if not file_uuid:
            raise RuntimeError(f"amo: загрузка файла не вернула uuid: {up.text[:200]}")

        r = requests.put(
            f"{self.base}/leads/{lead_id}/files",
            headers=self.headers,
            json=[{"file_uuid": file_uuid}],
            timeout=30,
        )
        r.raise_for_status()

    def add_note(self, entity: str, entity_id: int, text: str) -> None:
        body = [{"note_type": "common", "params": {"text": text}}]
        self._req("POST", f"/{entity}/{entity_id}/notes", json=body)

    def note_client(self, lead_id: int, object_id: str, text: str) -> None:
        self.add_note("leads", lead_id, f"[OBJ:{object_id}][CLIENT] {text}")

    def note_owner(self, lead_id: int, object_id: str, text: str) -> None:
        self.add_note("leads", lead_id, f"[OBJ:{object_id}][OWNER] {text}")

    # ---------- amoCRM tasks (GET/POST/PATCH /api/v4/tasks) ----------

    def list_tasks(
        self,
        *,
        entity_type: str = "leads",
        entity_id: int | None = None,
        is_completed: bool | None = False,
        page: int = 1,
        limit: int = 50,
    ) -> list[dict]:
        """Список задач. По умолчанию — открытые задачи сделки."""
        params: list[str] = [f"page={page}", f"limit={limit}"]
        if entity_type:
            params.append(f"filter[entity_type]={entity_type}")
        if entity_id is not None:
            params.append(f"filter[entity_id]={entity_id}")
        if is_completed is not None:
            params.append(f"filter[is_completed]={1 if is_completed else 0}")
        query = "&".join(params)
        data = self._req("GET", f"/tasks?{query}")
        return list((data or {}).get("_embedded", {}).get("tasks", []))

    def create_task(
        self,
        *,
        lead_id: int,
        text: str,
        complete_till: int,
        responsible_user_id: int,
        task_type_id: int | None = None,
    ) -> int:
        from .amo_tasks_config import default_task_type_id

        body = [{
            "entity_type": "leads",
            "entity_id": int(lead_id),
            "text": text,
            "complete_till": int(complete_till),
            "responsible_user_id": int(responsible_user_id),
            "task_type_id": int(task_type_id or default_task_type_id()),
            "is_completed": False,
        }]
        data = self._req("POST", "/tasks", json=body)
        return int(data["_embedded"]["tasks"][0]["id"])

    def update_task(self, task_id: int, **fields) -> None:
        if not fields:
            return
        self._req("PATCH", f"/tasks/{int(task_id)}", json=fields)

    def complete_task(self, task_id: int, result_text: str = "") -> None:
        payload: dict = {"is_completed": True}
        if result_text:
            payload["result"] = {"text": result_text[:4000]}
        self._req("PATCH", f"/tasks/{int(task_id)}", json=payload)

    def get_lead(self, lead_id: int) -> dict:
        return self._req("GET", f"/leads/{int(lead_id)}") or {}

    # ---------- contacts: custom fields + tags (role schema) ----------

    def list_contact_custom_fields(self) -> list[dict]:
        """GET /contacts/custom_fields (paginated)."""
        fields: list[dict] = []
        page = 1
        while True:
            data = self._req("GET", f"/contacts/custom_fields?limit=50&page={page}")
            fields.extend(
                list((data or {}).get("_embedded", {}).get("custom_fields", []) or [])
            )
            if not (data or {}).get("_links", {}).get("next"):
                break
            page += 1
        return fields

    def create_contact_custom_fields(self, fields: list[dict]) -> list[dict]:
        """POST /contacts/custom_fields — schema only, not contact data."""
        if not fields:
            return []
        data = self._req("POST", "/contacts/custom_fields", json=fields)
        return list((data or {}).get("_embedded", {}).get("custom_fields", []) or [])

    def update_contact_custom_fields(self, fields: list[dict]) -> list[dict]:
        """PATCH /contacts/custom_fields — e.g. add missing select enums."""
        if not fields:
            return []
        data = self._req("PATCH", "/contacts/custom_fields", json=fields)
        return list((data or {}).get("_embedded", {}).get("custom_fields", []) or [])

    def get_contact(self, contact_id: int, *, with_entities: str = "leads,tags") -> dict:
        path = f"/contacts/{int(contact_id)}"
        if with_entities:
            path = f"{path}?with={with_entities}"
        return self._req("GET", path) or {}

    def update_contact(self, contact_id: int, payload: dict) -> dict:
        """PATCH /contacts/{id} — used for role field + managed tags only."""
        body = dict(payload or {})
        body["id"] = int(contact_id)
        data = self._req("PATCH", "/contacts", json=[body])
        items = list((data or {}).get("_embedded", {}).get("contacts", []) or [])
        return items[0] if items else {}

    def list_contact_tags(self) -> list[dict]:
        """GET /contacts/tags (paginated)."""
        tags: list[dict] = []
        page = 1
        while True:
            data = self._req("GET", f"/contacts/tags?limit=250&page={page}")
            tags.extend(list((data or {}).get("_embedded", {}).get("tags", []) or []))
            if not (data or {}).get("_links", {}).get("next"):
                break
            page += 1
        return tags

    def create_contact_tags(self, names: list[str]) -> list[dict]:
        """POST /contacts/tags — create tag names only (no contact mutation)."""
        body = [{"name": n} for n in names if (n or "").strip()]
        if not body:
            return []
        data = self._req("POST", "/contacts/tags", json=body)
        return list((data or {}).get("_embedded", {}).get("tags", []) or [])

    # ---------- read-only diagnostics ----------

    def get_account(self, *, with_params: str = "") -> dict:
        path = "/account"
        if with_params:
            path = f"{path}?with={with_params}"
        return self._req("GET", path) or {}

    def list_users(self, *, page: int = 1, limit: int = 250) -> list[dict]:
        data = self._req("GET", f"/users?limit={int(limit)}&page={int(page)}")
        return list((data or {}).get("_embedded", {}).get("users", []))
