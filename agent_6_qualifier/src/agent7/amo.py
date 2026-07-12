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
        data = self._req("GET", f"/contacts?query={query}")
        items = data.get("_embedded", {}).get("contacts", []) if data else []
        return items[0] if items else None

    def find_open_lead(self, contact_id: int) -> int | None:
        """Открытая сделка контакта в нашей воронке — чтобы не плодить дубли.

        Открытая = не в системных стадиях «Успешно» (142) / «Закрыто» (143).
        Требует вызова ensure_pipeline() до этого (нужен self.pipeline_id).
        """
        data = self._req("GET", f"/contacts/{contact_id}?with=leads")
        lead_ids = [l["id"] for l in (data or {}).get("_embedded", {}).get("leads", [])]
        for lid in lead_ids:
            lead = self._req("GET", f"/leads/{lid}")
            if (lead.get("pipeline_id") == self.pipeline_id
                    and lead.get("status_id") not in (142, 143)):
                return lid
        return None

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

    def update_lead_fields(self, lead_id: int, lead: LeadProfile,
                           field_ids: dict[str, int]) -> None:
        """Дозаполняет карточку сделки фактами из профиля лида.

        Сделка создаётся при первом сообщении, когда известен только объект;
        даты/гости/бюджет/район появляются позже — синхронизируем их сюда.
        """
        from datetime import datetime, time

        def fv(name: str, value) -> dict:
            return {"field_id": field_ids[name], "values": [{"value": value}]}

        def ts(d) -> int:  # amo date-поля принимают unix timestamp
            return int(datetime.combine(d, time(12, 0)).timestamp())

        cf = []
        if lead.preferred_object_id:
            cf.append(fv("Объект ID", lead.preferred_object_id))
        if lead.check_in:
            cf.append(fv("Дата заезда", ts(lead.check_in)))
        if lead.check_out:
            cf.append(fv("Дата выезда", ts(lead.check_out)))
        if lead.budget is not None:
            cf.append(fv("Бюджет (мес)", int(lead.budget)))
            cf.append(fv("Допуск по бюджету %", int(lead.budget_tolerance_pct)))
        if lead.districts:
            cf.append(fv("Район", ", ".join(lead.districts)))
        if lead.guests:
            cf.append(fv("Гостей", lead.guests))
        if not cf:
            return
        body: dict = {"custom_fields_values": cf}
        if lead.budget:
            body["price"] = int(lead.budget)
        self._req("PATCH", f"/leads/{lead_id}", json=body)

    def add_note(self, entity: str, entity_id: int, text: str) -> None:
        body = [{"note_type": "common", "params": {"text": text}}]
        self._req("POST", f"/{entity}/{entity_id}/notes", json=body)

    def note_client(self, lead_id: int, object_id: str, text: str) -> None:
        self.add_note("leads", lead_id, f"[OBJ:{object_id}][CLIENT] {text}")

    def note_owner(self, lead_id: int, object_id: str, text: str) -> None:
        self.add_note("leads", lead_id, f"[OBJ:{object_id}][OWNER] {text}")
