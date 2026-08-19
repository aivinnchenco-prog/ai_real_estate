"""Персистентные сессии диалогов: data/sessions/{chat_id}.json.

Перезапуск агента больше не теряет состояние: профиль лида, выбранный объект,
какие вопросы уже заданы, id сделки в amo. Файл пишется после каждого сообщения.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date
from pathlib import Path

from .models import Availability, LeadProfile, Listing
from .qualifier import Session


def _json_default(o):
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, Availability):
        return o.value
    raise TypeError(f"не сериализуется: {type(o)}")


def _lead_from(d: dict) -> LeadProfile:
    for key in ("check_in", "check_out"):
        if d.get(key):
            d[key] = date.fromisoformat(d[key])
    return LeadProfile(**d)


def _listing_from(d: dict) -> Listing:
    if d.get("availability"):
        d["availability"] = Availability(d["availability"])
    if d.get("busy_until"):
        d["busy_until"] = date.fromisoformat(d["busy_until"])
    return Listing(**d)


class SessionStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: str) -> Path:
        safe = "".join(c for c in str(chat_id) if c.isalnum() or c in "-_")
        return self.root / f"{safe}.json"

    def save(self, session: Session) -> None:
        data = dataclasses.asdict(session)
        path = self._path(session.chat_id)
        # Атомарная запись: сессию могут сохранять два процесса (userbot и
        # скрипты Agent 8) — недописанный файл не должен попадать под чтение.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, default=_json_default, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(path)

    def find_awaiting_owner(self, tg_username: str) -> Session | None:
        """Сессия клиента, которая ждёт ответа владельца с этим TG-username.

        Так userbot отличает собственника от клиента: входящее сообщение
        от @username из колонки «Telegram контакт» — это ответ владельца.
        """
        want = tg_username.strip().lstrip("@").lower()
        if not want:
            return None
        for path in self.root.glob("*.json"):
            s = self.load(path.stem)
            if s is None:
                continue
            if self._session_awaiting_owner_for_username(s, want):
                return s
        return None

    def _session_awaiting_owner_for_username(self, s: Session, username: str) -> bool:
        if s.awaiting_owner and s.chosen is not None:
            owner = s.chosen.owner_telegram.strip().lstrip("@").lower()
            if owner == username:
                return True
        for item in s.pending_owner_requests:
            if not item.get("awaiting_owner"):
                continue
            chosen = item.get("chosen") or {}
            owner = str(chosen.get("owner_telegram") or "").strip().lstrip("@").lower()
            if owner == username:
                return True
        return False

    def find_awaiting_owner_by_object(self, object_id: str) -> Session | None:
        """Сессия клиента, ждущая ответа владельца по конкретному объекту.

        Запасной путь для реестра владельцев: у владельца мог смениться
        или скрыться @username, но объект из реестра известен.
        """
        if not object_id:
            return None
        for path in self.root.glob("*.json"):
            s = self.load(path.stem)
            if s is None:
                continue
            if self._session_awaiting_owner_for_object(s, object_id):
                return s
        return None

    def _session_awaiting_owner_for_object(self, s: Session, object_id: str) -> bool:
        if s.awaiting_owner and s.chosen is not None and s.chosen.object_id == object_id:
            return True
        for item in s.pending_owner_requests:
            if item.get("object_id") == object_id and item.get("awaiting_owner"):
                return True
        return False

    def load(self, chat_id: str) -> Session | None:
        path = self._path(chat_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            lead = data.pop("lead", None)
            chosen = data.pop("chosen", None)
            # Неизвестные ключи (например, из более новой версии кода)
            # не должны ронять загрузку — берём только поля Session.
            known = {f.name for f in dataclasses.fields(Session)}
            data = {k: v for k, v in data.items() if k in known}
            session = Session(**data)
            if lead:
                session.lead = _lead_from(lead)
            if chosen:
                session.chosen = _listing_from(chosen)
            return session
        except Exception as e:
            # Битый файл не должен ронять агента, но и молча терять диалог
            # нельзя: шлём алерт и оставляем копию для разбора.
            path.rename(path.with_suffix(".corrupt"))
            try:
                from .alerts import notify_error
                notify_error("sessions.load", repr(e),
                             f"сессия {chat_id} повреждена, диалог начат заново "
                             f"(копия: {path.with_suffix('.corrupt').name})")
            except Exception:
                pass
            return None
