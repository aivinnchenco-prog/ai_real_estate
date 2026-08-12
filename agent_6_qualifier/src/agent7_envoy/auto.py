"""Автоматический запуск Agent 7 Envoy из юзербота — без ручных скриптов.

Когда Qualifier решает «пора спросить владельца» (need_owner_check),
юзербот запускает auto_outreach фоновой задачей:

1. Свежий объект из Notion (контакты/календарь могли обновиться).
2. Реальная проверка календаря («Календарь»: Airbnb / iCal / Google-таблица).
3. Результат проверки -> Notion (availability, «Занято до», «Будущие брони»).
4. Даты закрыты -> клиенту сразу окно занятости (полное или частичное).
5. Даты открыты -> первое сообщение владельцу:
   - Telegram — автоматически (+ реестр владельцев + папка «Собственники»)
   - WhatsApp — автоматически только на controlled allowlist
     (AGENT7_CONTROLLED_OWNER_PHONES); иначе BLOCKED AT OWNER LIVE CONTACT
   - Airbnb DM / FB DM — алерт менеджеру.
"""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from agent6_qualifier import notion_store
from . import owner_registry
from agent6_qualifier.alerts import notify_error
from agent6_qualifier.telegram_folders import assign_role_folder
from agent6_qualifier.models import Availability, OwnerChannel
from agent6_qualifier.templates import client_object_busy, client_object_partial

from .calendar_check import notion_update_from_precheck
from .outreach import build_outreach_plan


def busy_message_for_client(precheck, listing, lead) -> str:
    """Текст клиенту, когда календарь закрыл его даты.

    Если желаемая дата заезда свободна, но весь срок не помещается —
    честно называем окно (N ночей) и дату полной свободы.
    """
    busy_until = precheck.busy_until(lead.check_in)
    free_from = busy_until + timedelta(days=1) if busy_until else None
    free_nights = precheck.free_nights_from(lead.check_in)
    if free_nights > 0:
        free_until = lead.check_in + timedelta(days=free_nights)
        return client_object_partial(
            listing.object_id,
            lead.check_in.strftime("%d.%m.%Y"),
            free_nights,
            free_until.strftime("%d.%m.%Y"),
            busy_until.strftime("%d.%m.%Y") if busy_until else "?",
            free_from.strftime("%d.%m.%Y") if free_from else "?",
        )
    return client_object_busy(
        listing.object_id,
        busy_until.strftime("%d.%m.%Y") if busy_until else "?",
        free_from.strftime("%d.%m.%Y") if free_from else "?",
    )


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _controlled_owner_phones() -> set[str]:
    from agent6_qualifier.messaging.wazzup_config import normalize_phone_e164_digits

    raw = os.getenv("AGENT7_CONTROLLED_OWNER_PHONES") or ""
    out: set[str] = set()
    for chunk in raw.replace(";", ",").split(","):
        digits = normalize_phone_e164_digits(chunk.strip())
        if digits:
            out.add(digits)
    return out


def _normalize_owner_phone(raw: str) -> str | None:
    from agent6_qualifier.messaging.wazzup_config import normalize_phone_e164_digits

    return normalize_phone_e164_digits(raw)


async def _send_client_text(client, session, msg: str) -> None:
    """Send to client via WhatsApp adapter or Telegram client."""
    send_wa = getattr(client, "send_whatsapp_text", None)
    if callable(send_wa):
        phone = session.lead.whatsapp or ""
        await send_wa(phone, msg)
        return
    chat_id = session.chat_id
    try:
        target = int(chat_id)
    except (TypeError, ValueError):
        target = chat_id
    await client.send_message(target, msg)


def _client_phone_digits(session) -> str | None:
    lead_wa = getattr(getattr(session, "lead", None), "whatsapp", "") or ""
    chat_id = str(getattr(session, "chat_id", "") or "")
    return _normalize_owner_phone(lead_wa) or _normalize_owner_phone(chat_id)


def _manual_owner_role_conflict(owner_digits: str) -> str | None:
    """Return OWNER_ROLE_MANUAL_CONFLICT if MANUAL lock is incompatible with OWNER/AGENT.

    Compatible MANUAL OWNER/AGENT → None (allow).
    Mirror/store failures → None (non-blocking).
    """
    try:
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        cr_src = root / "contact_role" / "src"
        if str(cr_src) not in sys.path:
            sys.path.insert(0, str(cr_src))
        from contact_role.roles import CanonicalRole
        from contact_role.sources import RoleSource
        from contact_role.state import ContactRoleStore

        store = ContactRoleStore()
        state = store.get_by_phone(owner_digits)
        if state is None:
            return None
        source = RoleSource.parse(state.role_source)
        if source is not RoleSource.MANUAL and not state.locked_by_manual_override:
            return None
        role = CanonicalRole.parse(state.canonical_role)
        if role in {CanonicalRole.OWNER, CanonicalRole.AGENT}:
            return None
        return "OWNER_ROLE_MANUAL_CONFLICT"
    except Exception:
        return None


async def auto_outreach(client, session, store, amo) -> None:
    """Полный цикл Agent 7 Envoy для одной сессии. Ошибки не роняют юзербота."""
    live = _env_bool("AGENT7_LIVE_OUTREACH_ENABLED", False)
    if not live:
        print(
            f"[agent7] OWNER_CHECK_READY object={session.lead.preferred_object_id} "
            f"chat={session.chat_id} (AGENT7_LIVE_OUTREACH_ENABLED=false — suppressed)"
        )
        store.save(session)
        return

    lead = session.lead
    chat_id = session.chat_id
    try:
        listing = await asyncio.to_thread(
            notion_store.find_by_object_id, lead.preferred_object_id)
        if listing is None:
            notify_error("agent8.auto", f"объект {lead.preferred_object_id} "
                         "не найден в Notion", f"клиент chat_id={chat_id}")
            return
        session.chosen = listing

        # build_outreach_plan внутри ходит в календарь (Playwright/HTTP) — в поток.
        plan = await asyncio.to_thread(build_outreach_plan, listing, lead)

        if plan.precheck is not None:
            print(f"[agent8] календарь {listing.object_id}: "
                  f"available={plan.precheck.available} ({plan.precheck.note or 'ok'})")
            upd = notion_update_from_precheck(plan.precheck, lead.check_in)
            if upd and listing.page_id:
                try:
                    await asyncio.to_thread(
                        notion_store.update_availability,
                        listing.page_id, upd["status"],
                        busy_until=upd.get("busy_until"),
                        future_bookings=upd.get("future_bookings", ""),
                    )
                except Exception as e:
                    notify_error("notion.availability", str(e),
                                 f"объект {listing.object_id}")

        # --- даты закрыты: клиенту сразу занятость, владельцу не пишем ---
        if plan.skip_reason and plan.precheck and plan.precheck.available is False:
            msg = busy_message_for_client(plan.precheck, listing, lead)
            session.awaiting_owner = False
            session.owner_verdict = "busy"
            session.awaiting_alt_consent = True
            busy_until = plan.precheck.busy_until(lead.check_in)
            if busy_until:
                session.chosen.availability = Availability.BUSY
                session.chosen.busy_until = busy_until
            await _send_client_text(client, session, msg)
            session.history.append({"role": "assistant", "text": msg})
            store.save(session)
            print(f"[out] {chat_id}: {msg[:80]}")
            if amo is not None and session.amo_lead_id:
                await asyncio.to_thread(
                    amo.note_owner, session.amo_lead_id, listing.object_id,
                    f"Календарь: даты закрыты ({plan.precheck.note})")
            return

        # --- владельцу не написать: контактов нет ---
        if plan.channel is None:
            notify_error("agent8.auto", f"владельцу не написать: {plan.skip_reason}",
                         f"объект {listing.object_id}, клиент chat_id={chat_id}")
            return

        # CONTACT_OUTREACH_STARTED — canonical role BEFORE first message.
        # Mirror failures must never block outreach send.
        try:
            from agent6_qualifier.messaging.contact_role_hook import (
                on_contact_outreach_started,
            )

            on_contact_outreach_started(
                listing=listing,
                phone=(
                    listing.owner_whatsapp
                    or (plan.contact if plan.channel == OwnerChannel.WHATSAPP else "")
                ),
                channel=plan.channel.value if plan.channel else "",
            )
        except Exception:
            pass

        # --- Telegram: отправляем автоматически ---
        if plan.channel == OwnerChannel.TELEGRAM:
            username = plan.contact.lstrip("@")
            entity = await client.get_entity(username)
            await client.send_message(entity, plan.first_message)
            print(f"[agent8] владельцу @{username}: {plan.first_message[:80]}")

            owner_registry.mark_owner(
                tg_username=username,
                tg_chat_id=str(getattr(entity, "id", "") or ""),
                object_id=listing.object_id,
            )
            await assign_role_folder(client, entity, listing.owner_agent_type)

            session.awaiting_owner = True
            store.save(session)
            if amo is not None and session.amo_lead_id:
                await asyncio.to_thread(
                    amo.note_owner, session.amo_lead_id, listing.object_id,
                    f"Запрос владельцу (telegram): {plan.first_message[:150]}")
                from agent6_qualifier.amo_task_service import AmoTaskService
                AmoTaskService(amo).on_owner_outreach_sent(session)
            return

        # --- WhatsApp: только controlled owner contact ---
        if plan.channel == OwnerChannel.WHATSAPP:
            owner_phone_raw = (
                listing.owner_whatsapp
                or plan.contact
                or ""
            )
            owner_digits = _normalize_owner_phone(owner_phone_raw)
            controlled = _controlled_owner_phones()
            if not controlled:
                print(
                    "[agent7] BLOCKED AT OWNER LIVE CONTACT: "
                    "AGENT7_CONTROLLED_OWNER_PHONES empty"
                )
                notify_error(
                    "agent7.owner_blocked",
                    "BLOCKED AT OWNER LIVE CONTACT",
                    f"object={listing.object_id} no controlled owner allowlist",
                )
                store.save(session)
                return
            if not owner_digits or owner_digits not in controlled:
                print(
                    "[agent7] BLOCKED AT OWNER LIVE CONTACT: "
                    f"owner not on controlled list object={listing.object_id}"
                )
                notify_error(
                    "agent7.owner_blocked",
                    "BLOCKED AT OWNER LIVE CONTACT",
                    f"object={listing.object_id} owner not controlled",
                )
                store.save(session)
                return

            client_digits = _client_phone_digits(session)
            if client_digits and owner_digits and client_digits == owner_digits:
                print(
                    "[agent7] CLIENT_OWNER_IDENTITY_CONFLICT: "
                    f"object={listing.object_id}"
                )
                notify_error(
                    "agent7.owner_blocked",
                    "CLIENT_OWNER_IDENTITY_CONFLICT",
                    f"object={listing.object_id} client phone equals owner phone",
                )
                store.save(session)
                return

            manual_conflict = _manual_owner_role_conflict(owner_digits)
            if manual_conflict:
                print(
                    f"[agent7] {manual_conflict}: object={listing.object_id}"
                )
                notify_error(
                    "agent7.owner_blocked",
                    manual_conflict,
                    f"object={listing.object_id} conflicting MANUAL role lock",
                )
                store.save(session)
                return

            send_wa = getattr(client, "send_whatsapp_text", None)
            if not callable(send_wa):
                notify_error(
                    "agent8.manual_send",
                    f"WhatsApp owner send unavailable on this client "
                    f"({owner_digits})",
                    f"объект {listing.object_id}, текст: {plan.first_message[:200]}",
                )
                store.save(session)
                return

            from agent7_envoy.owner_request_store import OwnerRequestStore

            req_store = OwnerRequestStore()
            owner_req = req_store.create(
                owner_phone=owner_digits,
                object_id=listing.object_id,
                client_session_chat_id=str(session.chat_id),
                channel="whatsapp",
            )
            session.owner_request_id = owner_req.owner_request_id

            e164 = f"+{owner_digits}"
            await send_wa(e164, plan.first_message)
            print(f"[agent8] владельцу WA {e164[-4:]}: {plan.first_message[:80]}")
            req_store.mark_awaiting(owner_req.owner_request_id)
            owner_registry.mark_owner(
                whatsapp=e164,
                object_id=listing.object_id,
                channel="whatsapp",
                owner_request_id=owner_req.owner_request_id,
                client_session_chat_id=str(session.chat_id),
            )
            try:
                from agent6_qualifier.messaging.e2e_report import record_agent7_outbound

                record_agent7_outbound(
                    object_id=listing.object_id,
                    owner_masked=f"+**{owner_digits[-3:]}",
                )
            except Exception:
                pass
            session.awaiting_owner = True
            store.save(session)
            if amo is not None and session.amo_lead_id:
                await asyncio.to_thread(
                    amo.note_owner, session.amo_lead_id, listing.object_id,
                    f"Запрос владельцу (whatsapp): {plan.first_message[:150]}")
                from agent6_qualifier.amo_task_service import AmoTaskService
                AmoTaskService(amo).on_owner_outreach_sent(session)
            return

        # --- Airbnb Messages / Facebook Messenger: source-native transport ---
        if plan.channel in (
            OwnerChannel.AIRBNB,
            OwnerChannel.AIRBNB_MESSAGES,
            OwnerChannel.FB_MARKETPLACE,
            OwnerChannel.FACEBOOK_MESSENGER,
        ):
            from agent7_envoy.messaging.base import SendOutcome
            from agent7_envoy.messaging.dispatch import (
                dispatch_outreach_plan,
                source_native_live_allowed,
            )

            if not source_native_live_allowed(plan.channel):
                # Safe default: dry-run readiness probe, never live-send
                dispatch = await asyncio.to_thread(
                    dispatch_outreach_plan,
                    plan,
                    client_session_chat_id=str(session.chat_id),
                    dry_run=True,
                )
                outcome = dispatch.result.outcome
                print(
                    f"[agent7] source-native dry-run channel={plan.channel.value} "
                    f"object={listing.object_id} outcome={outcome.value} "
                    f"blocker={dispatch.result.blocker or '-'}"
                )
                notify_error(
                    "agent8.manual_send",
                    f"Source-native channel gated off "
                    f"({plan.channel.value}: {plan.contact}) outcome={outcome.value}",
                    f"объект {listing.object_id}, текст: {plan.first_message[:200]}",
                )
                store.save(session)
                return

            dispatch = await asyncio.to_thread(
                dispatch_outreach_plan,
                plan,
                client_session_chat_id=str(session.chat_id),
                dry_run=False,
            )
            if dispatch.owner_request is not None:
                session.owner_request_id = dispatch.owner_request.owner_request_id
            if dispatch.result.outcome == SendOutcome.SENT:
                session.awaiting_owner = True
                store.save(session)
                from agent7_envoy.crm_business_sync import (
                    attempt_raw_chat_mirror,
                    sync_owner_outreach_started,
                )

                mirror = attempt_raw_chat_mirror(channel=plan.channel)
                if mirror.raw_chat_skipped_expected:
                    print(
                        f"[agent7.crm] channel={plan.channel.value} "
                        f"crm_visibility=BUSINESS_EVENTS_ONLY "
                        f"raw_chat_mirror=SKIPPED_EXPECTED"
                    )
                sync_owner_outreach_started(
                    amo,
                    session,
                    channel=plan.channel,
                    object_id=listing.object_id,
                    owner_request_id=(
                        dispatch.owner_request.owner_request_id
                        if dispatch.owner_request
                        else ""
                    ),
                )
                return

            notify_error(
                "agent8.manual_send",
                f"Source-native send blocked "
                f"({plan.channel.value}): {dispatch.result.blocker or dispatch.result.outcome.value}",
                f"объект {listing.object_id}, текст: {plan.first_message[:200]}",
            )
            store.save(session)
            return

        # --- Unknown channel fallback ---
        notify_error(
            "agent8.manual_send",
            f"Отправьте владельцу вручную ({plan.channel.value}: {plan.contact})",
            f"объект {listing.object_id}, текст: {plan.first_message[:200]}",
        )
        if amo is not None and session.amo_lead_id:
            await asyncio.to_thread(
                amo.note_owner, session.amo_lead_id, listing.object_id,
                f"ТРЕБУЕТ РУЧНОЙ ОТПРАВКИ ({plan.channel.value} {plan.contact}): "
                f"{plan.first_message[:150]}")
    except Exception as e:
        notify_error("agent8.auto", repr(e),
                     f"авто-запрос владельцу не выполнен, chat_id={chat_id}")
