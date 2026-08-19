"""amoCRM operational task lifecycle (not asyncio.create_task)."""

from __future__ import annotations

import time
from typing import Any, Callable

from .alerts import notify_error, notify_manager
from .amo import AmoClient
from .amo_tasks_config import (
    amo_tasks_enabled,
    client_followup_hours,
    format_task_text,
    manager_responsible_user_id,
    need_human_minutes,
    owner_followup_hours,
    parse_task_key,
)
from .amo_task_state import (
    client_auto_followups_sent,
    increment_client_auto_followups,
    mark_escalated,
    was_escalated,
)


NotifyError = Callable[[str, str, str], None]


class AmoTaskService:
  """High-level task dedup, SLA deadlines, reconciliation."""

  def __init__(self, amo: AmoClient, *, notify_error_fn: NotifyError | None = None):
      self.amo = amo
      self._notify_error = notify_error_fn or notify_error

  def _safe(self, op: str, fn: Callable[[], Any], context: str = "") -> Any:
      try:
          return fn()
      except Exception as exc:
          self._notify_error("amo.task", str(exc), context or op)
          return None

  def resolve_responsible_user_id(
      self, lead_id: int, *, for_manager: bool = False,
  ) -> int | None:
      if for_manager:
          return manager_responsible_user_id()
      lead = self._safe("get_lead", lambda: self.amo.get_lead(lead_id), f"lead #{lead_id}")
      if lead and lead.get("responsible_user_id"):
          return int(lead["responsible_user_id"])
      from .amo_tasks_config import default_responsible_user_id
      return default_responsible_user_id()

  def list_open_tasks(self, lead_id: int) -> list[dict]:
      tasks = self._safe(
          "list_tasks",
          lambda: self.amo.list_tasks(entity_id=lead_id, is_completed=False),
          f"lead #{lead_id}",
      )
      return tasks or []

  def find_open_task(self, lead_id: int, task_key: str) -> dict | None:
      for task in self.list_open_tasks(lead_id):
          if parse_task_key(task.get("text") or "") == task_key:
              return task
      return None

  def ensure_task(
      self,
      lead_id: int,
      task_key: str,
      body: str,
      *,
      deadline_ts: int,
      for_manager: bool = False,
  ) -> int | None:
      if not amo_tasks_enabled():
          return None
      existing = self.find_open_task(lead_id, task_key)
      text = format_task_text(task_key, body)
      responsible = self.resolve_responsible_user_id(lead_id, for_manager=for_manager)
      if responsible is None:
          self._notify_error("amo.task", "responsible_user_id missing", f"lead #{lead_id}")
          return None
      if existing:
          tid = int(existing["id"])
          updates: dict[str, Any] = {}
          if existing.get("text") != text:
              updates["text"] = text
          if int(existing.get("complete_till") or 0) != int(deadline_ts):
              updates["complete_till"] = int(deadline_ts)
          if updates:
              self._safe("update_task", lambda: self.amo.update_task(tid, **updates),
                         f"task {tid}")
          return tid
      tid = self._safe(
          "create_task",
          lambda: self.amo.create_task(
              lead_id=lead_id,
              text=text,
              complete_till=deadline_ts,
              responsible_user_id=responsible,
          ),
          f"lead #{lead_id} {task_key}",
      )
      return int(tid) if tid else None

  def complete_task(
      self, lead_id: int, task_key: str, result_text: str = "",
  ) -> bool:
      task = self.find_open_task(lead_id, task_key)
      if not task:
          return False
      tid = int(task["id"])
      self._safe(
          "complete_task",
          lambda: self.amo.complete_task(tid, result_text),
          f"task {tid}",
      )
      return True

  def complete_operational_tasks(self, lead_id: int, keys: set[str], result: str) -> None:
      for task in self.list_open_tasks(lead_id):
          key = parse_task_key(task.get("text") or "")
          if key in keys:
              tid = int(task["id"])
              self._safe(
                  "complete_task",
                  lambda tid=tid, result=result: self.amo.complete_task(tid, result),
                  f"task {tid}",
              )

  # ---------- lifecycle hooks ----------

  def on_owner_outreach_sent(self, session) -> int | None:
      lead_id = session.amo_lead_id
      if not lead_id:
          return None
      lead = session.lead
      obj = lead.preferred_object_id or "-"
      dates = ""
      if lead.check_in:
          dates = lead.check_in.strftime("%d.%m.%Y")
          if lead.check_out:
              dates += f" — {lead.check_out.strftime('%d.%m.%Y')}"
      body = (
          f"Проверить ответ владельца по {obj}\n"
          f"Даты: {dates or '-'}\n"
          f"Клиент: {lead.name or session.chat_id}\n"
          f"Ждём: свободно/занято/условия"
      )
      deadline = int(time.time()) + owner_followup_hours() * 3600
      return self.ensure_task(lead_id, "owner_followup", body, deadline_ts=deadline)

  def on_owner_response(self, session, verdict_status: str) -> None:
      if verdict_status not in ("free", "busy", "conditions_changed", "conditions"):
          return
      lead_id = session.amo_lead_id
      if not lead_id:
          return
      label = "conditions_changed" if verdict_status == "conditions" else verdict_status
      self.complete_task(lead_id, "owner_followup", f"Ответ владельца получен: {label}")

  def on_need_human(
      self,
      session,
      client_message: str,
      *,
      notify_fn=notify_manager,
      sender: Any | None = None,
  ) -> int | None:
      lead_id = session.amo_lead_id
      lead = session.lead
      body = (
          f"Клиент требует ответа менеджера\n"
          f"Объект: {lead.preferred_object_id or '-'}\n"
          f"Сделка: #{lead_id or '-'}\n"
          f"ФИО: {lead.full_name or '-'}, гражданство: {lead.citizenship or '-'}\n"
          f"Последнее сообщение: {(client_message or '')[:400]}"
      )
      deadline = int(time.time()) + need_human_minutes() * 60
      task_id = None
      if lead_id:
          task_id = self.ensure_task(
              lead_id, "need_human", body,
              deadline_ts=deadline, for_manager=True,
          )
      uname = getattr(sender, "username", "") or "" if sender else ""
      who = f"@{uname}" if uname else f"chat_id={session.chat_id}"
      telegram_text = (
          f"Клиент {who} ожидает ответа менеджера.\n"
          f"Объект: {lead.preferred_object_id or '-'}, "
          f"сделка #{lead_id or '-'}\n"
          f"ФИО: {lead.full_name or '-'}, "
          f"гражданство: {lead.citizenship or '-'}\n"
          f"Сообщение: {(client_message or '')[:200]}"
      )
      try:
          notify_fn(
              telegram_text,
              dedup_key=str(session.chat_id),
          )
      except Exception as exc:
          self._notify_error("manager.alert", str(exc), f"chat {session.chat_id}")
      return task_id

  def on_client_outbound_awaiting_response(self, session, reply_text: str) -> int | None:
      lead_id = session.amo_lead_id
      if not lead_id:
          return None
      lead = session.lead
      body = (
          f"Ждём ответ клиента\n"
          f"Объект: {lead.preferred_object_id or '-'}\n"
          f"Chat: {session.chat_id}\n"
          f"Последнее исходящее: {(reply_text or '')[:300]}"
      )
      deadline = int(time.time()) + client_followup_hours() * 3600
      return self.ensure_task(lead_id, "client_followup", body, deadline_ts=deadline)

  def on_client_inbound(self, session) -> None:
      lead_id = session.amo_lead_id
      if not lead_id:
          return
      self.complete_task(lead_id, "client_followup", "Клиент ответил")

  def reconcile_stage(self, lead_id: int, stage_name: str) -> None:
      """Close stale operational tasks incompatible with current stage."""
      if stage_name in ("Согласование условий", "Бронь подтверждена"):
          self.complete_operational_tasks(
              lead_id, {"owner_followup"},
              f"Стадия: {stage_name}",
          )
      if stage_name == "Бронь подтверждена":
          self.complete_operational_tasks(
              lead_id, {"client_followup", "conditions_approval"},
              "Бронь подтверждена",
          )

  def process_overdue_task(self, task: dict, session_lookup: Callable[[int], Any] | None = None) -> None:
      """Idempotent overdue policy for SLA watcher."""
      key = parse_task_key(task.get("text") or "")
      if not key:
          return
      lead_id = int(task.get("entity_id") or 0)
      if not lead_id:
          return
      if key == "owner_followup":
          self._escalate_owner_overdue(lead_id, task)
      elif key == "client_followup":
          self._escalate_client_overdue(lead_id, task, session_lookup)

  def _escalate_owner_overdue(self, lead_id: int, task: dict) -> None:
      if was_escalated(lead_id, "owner_followup"):
          return
      mark_escalated(lead_id, "owner_followup")
      body = (
          f"Просрочен SLA ответа владельца (2ч)\n"
          f"Сделка #{lead_id}\n"
          f"Исходная задача: #{task.get('id')}"
      )
      notify_manager(body, dedup_key=f"owner_overdue:{lead_id}")
      self.ensure_task(
          lead_id, "need_human", body,
          deadline_ts=int(time.time()) + need_human_minutes() * 60,
          for_manager=True,
      )

  def _escalate_client_overdue(
      self, lead_id: int, task: dict,
      session_lookup: Callable[[int], Any] | None,
  ) -> None:
      from .amo_tasks_config import max_automatic_client_followups
      if was_escalated(lead_id, "client_followup"):
          return
      if client_auto_followups_sent(lead_id) >= max_automatic_client_followups():
          mark_escalated(lead_id, "client_followup")
          notify_manager(
              f"Клиент не ответил (SLA 24ч), сделка #{lead_id}. Нужен review.",
              dedup_key=f"client_overdue:{lead_id}",
          )
          return
      increment_client_auto_followups(lead_id)
      mark_escalated(lead_id, "client_followup")
      self.complete_task(lead_id, "client_followup", "Overdue: эскалация")
      notify_manager(
          f"Клиент не ответил в срок (24ч), сделка #{lead_id}. "
          f"Автоматический follow-up не настроен — review.",
          dedup_key=f"client_overdue:{lead_id}",
      )
