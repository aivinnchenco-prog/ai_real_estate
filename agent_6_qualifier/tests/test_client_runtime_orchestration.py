"""Regression tests for client-message runtime orchestration in agent7.tg_userbot.main."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent8_notary.booking_doc import generate_booking_doc
from agent8_notary.service import NotaryBookingResult, process_confirmed_booking
from client_runtime_fixtures import (
    FakeStore,
    RuntimeHarness,
    make_client_session,
    make_event,
    make_sender,
    make_turn,
)

NOTARY_CAPTION = (
    "Соглашение о бронировании (заявка). Оплаты по нему "
    "нет — итоговые условия зафиксируем в основном "
    "договоре после просмотра."
)


@pytest.fixture
def runtime(monkeypatch):
    """Boot tg_userbot.main() far enough to capture the nested on_message handler."""
    import agent6_qualifier.tg_userbot as tg

    store = FakeStore()
    harness = RuntimeHarness(
        on_message=None,  # type: ignore[arg-type]
        client=MagicMock(),
        amo=MagicMock(),
        store=store,
        qualifier=MagicMock(),
        main_task=None,  # type: ignore[arg-type]
    )
    harness.amo.ensure_pipeline.return_value = {
        "Запрос владельцу": 11,
        "Бронь подтверждена": 22,
        "Новый лид": 33,
    }
    harness.amo.ensure_lead_fields.return_value = {}
    harness.amo.attach_file = MagicMock()

    handler_holder: dict = {}
    hang = asyncio.Event()

    def capture_on(*args, **kwargs):
        def decorator(fn):
            handler_holder["on_message"] = fn
            return fn
        return decorator

    harness.client.on = capture_on
    harness.client.start = AsyncMock()
    harness.client.get_me = AsyncMock(
        return_value=MagicMock(username="agent7", first_name="Agent"),
    )
    harness.client.run_until_disconnected = hang.wait
    harness.client.send_file = AsyncMock()

    monkeypatch.setattr(tg, "_store", store)
    monkeypatch.setattr(tg, "_sessions", {})
    monkeypatch.setattr(tg, "_outreach_inflight", set())
    monkeypatch.setattr(tg, "make_client", lambda: harness.client)
    monkeypatch.setattr(tg, "load_env", lambda: None)
    monkeypatch.setattr(tg, "run_schema_check", lambda *a, **k: None)
    monkeypatch.setattr(tg, "AmoClient", lambda: harness.amo)
    monkeypatch.setattr(tg, "Qualifier", lambda *a, **k: harness.qualifier)
    monkeypatch.setattr(tg.notion_store, "find_by_object_id", lambda *_: None)
    monkeypatch.setattr(tg.notion_store, "fetch_all_listings", lambda: [])
    monkeypatch.setattr(tg.notion_store, "fetch_all_pages", lambda: [])
    monkeypatch.setattr(tg.notion_store, "find_by_tg_post", lambda *_a, **_k: None)
    monkeypatch.setattr(tg, "handle_owner_message", AsyncMock(return_value=False))
    monkeypatch.setattr(tg.brain, "extract_lead_update", lambda *a, **k: {})
    monkeypatch.setattr(tg.brain, "polish_reply", lambda draft, language, name: draft)
    monkeypatch.setattr(tg, "humanized_respond", AsyncMock())
    monkeypatch.setattr(tg, "add_to_folder", AsyncMock())
    monkeypatch.setattr(
        tg,
        "notify_error",
        lambda component, error, context="": harness.notify_errors.append(
            (component, error, context),
        ),
    )
    monkeypatch.setattr(tg, "ensure_amo_lead", MagicMock())

    real_create_task = asyncio.create_task

    def track_create_task(coro, **kwargs):
        task = real_create_task(coro, **kwargs)
        if getattr(coro, "cr_code", None) and coro.cr_code.co_name == "_run_outreach":
            harness.outreach_tasks.append(task)
        return task

    monkeypatch.setattr(tg.asyncio, "create_task", track_create_task)

    async def _auto_outreach(client, session, store, amo):
        harness.outreach_calls.append((client, session, store, amo))

    monkeypatch.setattr("agent7_envoy.auto.auto_outreach", _auto_outreach)

    async def _process_confirmed_booking(session, contact, **kwargs):
        harness.notary_calls.append((session, contact, kwargs))
        return NotaryBookingResult(
            sent_to_client=False,
            doc_path=Path("/tmp/fake-booking.docx"),
        )

    monkeypatch.setattr(
        "agent8_notary.service.process_confirmed_booking",
        _process_confirmed_booking,
    )

    def _notify_manager(text, *, dedup_key=""):
        harness.manager_calls.append((text, dedup_key))

    monkeypatch.setattr("agent6_qualifier.alerts.notify_manager", _notify_manager)

    async def _boot():
        main_task = asyncio.create_task(tg.main())
        for _ in range(100):
            if "on_message" in handler_holder:
                harness.on_message = handler_holder["on_message"]
                harness.main_task = main_task
                harness.humanized_respond = tg.humanized_respond
                return
            await asyncio.sleep(0.01)
        main_task.cancel()
        raise RuntimeError("on_message handler was not captured from main()")

    asyncio.run(_boot())

    yield harness

    async def _shutdown():
        hang.set()
        await harness.shutdown()

    asyncio.run(_shutdown())


def _prepare_session(runtime: RuntimeHarness, turn, *, text: str = "Привет", sender=None):
    session = make_client_session()
    runtime.store._by_chat[session.chat_id] = session
    import agent6_qualifier.tg_userbot as tg
    tg._sessions[session.chat_id] = session
    runtime.qualifier.handle_message.return_value = turn
    event = make_event(text=text)
    if sender is None:
        sender = make_sender()
    event.get_sender.return_value = sender
    return session, event, sender


def _expected_manager_message(session, sender, text: str) -> str:
    uname = getattr(sender, "username", "") or ""
    who = f"@{uname}" if uname else f"chat_id={session.chat_id}"
    return (
        f"Клиент {who} ожидает ответа менеджера.\n"
        f"Объект: {session.lead.preferred_object_id or '-'}, "
        f"сделка #{session.amo_lead_id or '-'}\n"
        f"ФИО: {session.lead.full_name or '-'}, "
        f"гражданство: {session.lead.citizenship or '-'}\n"
        f"Сообщение: {text[:200]}"
    )


def test_captured_handler_is_production_on_message(runtime):
    assert runtime.on_message.__name__ == "on_message"
    assert runtime.on_message.__qualname__ == "main.<locals>.on_message"


def test_need_owner_check_starts_auto_outreach_once(runtime):
    """Envoy gate: need_owner_check launches auto_outreach with current session."""
    turn = make_turn(need_owner_check=True)
    session, event, sender = _prepare_session(runtime, turn)

    asyncio.run(runtime.run(event, sender))
    asyncio.run(runtime.drain_outreach_tasks())

    assert len(runtime.outreach_calls) == 1
    client, sess, store, amo = runtime.outreach_calls[0]
    assert client is runtime.client
    assert sess is session
    assert store is runtime.store
    assert amo is runtime.amo
    assert len(runtime.store.saved) == 1
    assert runtime.store.saved[0] is session
    runtime.assert_outreach_inflight_clear(session.chat_id)


def test_need_owner_check_false_skips_auto_outreach(runtime):
    turn = make_turn(need_owner_check=False)
    session, event, sender = _prepare_session(runtime, turn)
    import agent6_qualifier.tg_userbot as tg
    before = set(tg._outreach_inflight)

    asyncio.run(runtime.run(event, sender))
    asyncio.run(runtime.drain_outreach_tasks())

    assert runtime.outreach_calls == []
    assert tg._outreach_inflight == before


def test_owner_verdict_blocks_outreach(runtime):
    turn = make_turn(need_owner_check=True)
    session = make_client_session(owner_verdict="free")
    runtime.store._by_chat[session.chat_id] = session
    import agent6_qualifier.tg_userbot as tg
    tg._sessions[session.chat_id] = session
    before = set(tg._outreach_inflight)
    runtime.qualifier.handle_message.return_value = turn
    event = make_event()
    sender = make_sender()
    event.get_sender.return_value = sender

    asyncio.run(runtime.run(event, sender))
    asyncio.run(runtime.drain_outreach_tasks())

    assert runtime.outreach_calls == []
    assert tg._outreach_inflight == before
    runtime.amo.update_lead_status.assert_not_called()


def test_outreach_inflight_blocks_duplicate_launch(runtime):
    turn = make_turn(need_owner_check=True)
    session, event, sender = _prepare_session(runtime, turn)
    import agent6_qualifier.tg_userbot as tg
    tg._outreach_inflight.add(session.chat_id)

    asyncio.run(runtime.run(event, sender))
    asyncio.run(runtime.drain_outreach_tasks())

    assert runtime.outreach_calls == []


def test_outreach_amo_stage_before_task_and_no_new_lead(runtime, monkeypatch):
    order: list[str] = []

    def _track_stage(lead_id, stage_id):
        order.append("amo_stage")

    real_create_task = asyncio.create_task

    def _track_create_task(coro, **kwargs):
        if getattr(coro, "cr_code", None) and coro.cr_code.co_name == "_run_outreach":
            order.append("create_task")
        return real_create_task(coro, **kwargs)

    import agent6_qualifier.tg_userbot as tg
    monkeypatch.setattr(tg.asyncio, "create_task", _track_create_task)
    runtime.amo.update_lead_status.side_effect = _track_stage

    turn = make_turn(need_owner_check=True)
    session, event, sender = _prepare_session(runtime, turn)

    asyncio.run(runtime.run(event, sender))
    asyncio.run(runtime.drain_outreach_tasks())

    assert order.index("amo_stage") < order.index("create_task")
    runtime.amo.update_lead_status.assert_any_call(session.amo_lead_id, 11)
    runtime.amo.create_lead.assert_not_called()
    runtime.assert_outreach_inflight_clear(session.chat_id)


def test_outreach_amo_stage_error_still_starts_outreach(runtime, monkeypatch):
    runtime.amo.ensure_pipeline.side_effect = RuntimeError("stage down")
    turn = make_turn(need_owner_check=True)
    session, event, sender = _prepare_session(runtime, turn)

    asyncio.run(runtime.run(event, sender))
    asyncio.run(runtime.drain_outreach_tasks())

    assert any(
        err == ("amo.stage", "stage down", "не удалось сменить стадию")
        for err in runtime.notify_errors
    )
    assert len(runtime.outreach_calls) == 1
    runtime.amo.create_lead.assert_not_called()
    runtime.assert_outreach_inflight_clear(session.chat_id)


def test_outreach_task_failure_does_not_break_client_flow(runtime, monkeypatch):
    async def _boom(client, session, store, amo):
        raise RuntimeError("outreach down")

    monkeypatch.setattr("agent7_envoy.auto.auto_outreach", _boom)
    turn = make_turn(need_owner_check=True)
    session, event, sender = _prepare_session(runtime, turn)

    asyncio.run(runtime.run(event, sender))
    asyncio.run(runtime.drain_outreach_tasks(absorb_errors=True))

    assert len(runtime.store.saved) == 1
    runtime.humanized_respond.assert_awaited_once()
    runtime.assert_outreach_inflight_clear(session.chat_id)


def test_booking_confirmed_runs_notary(runtime):
    turn = make_turn(booking_confirmed=True, handoff_to_human=True)
    session, event, sender = _prepare_session(runtime, turn, text="Да, бронирую")

    asyncio.run(runtime.run(event, sender))

    assert len(runtime.notary_calls) == 1
    sess, contact, kwargs = runtime.notary_calls[0]
    assert sess is session
    assert contact == "Telegram: @client_user"
    assert kwargs["generate_doc"] is generate_booking_doc
    assert kwargs["amo_lead_id"] == session.amo_lead_id
    assert kwargs["attach_file"] is runtime.amo.attach_file
    on_generation_error = kwargs["on_generation_error"]
    on_generation_error(RuntimeError("boom"))
    assert any(err[0] == "booking_doc" for err in runtime.notify_errors)
    runtime.amo.create_lead.assert_not_called()

    send_doc = kwargs["send_doc"]
    asyncio.run(send_doc(Path("/tmp/fake-booking.docx")))
    runtime.client.send_file.assert_awaited_once_with(
        event.chat_id,
        "/tmp/fake-booking.docx",
        caption=NOTARY_CAPTION,
        reply_to=event.message.id,
    )


def test_notary_contact_fallback_without_username(runtime):
    turn = make_turn(booking_confirmed=True)
    session, event, sender = _prepare_session(
        runtime, turn, sender=make_sender(username=""),
    )

    asyncio.run(runtime.run(event, sender))

    _, contact, _ = runtime.notary_calls[0]
    assert contact == f"Telegram id {session.chat_id}"


def test_booking_not_confirmed_skips_notary(runtime):
    turn = make_turn(booking_confirmed=False)
    _, event, sender = _prepare_session(runtime, turn)

    asyncio.run(runtime.run(event, sender))

    assert runtime.notary_calls == []


def test_booking_stage_update_after_notary(runtime, monkeypatch):
    order: list[str] = []

    async def _track_notary(session, contact, **kwargs):
        order.append("notary")
        return NotaryBookingResult()

    def _track_stage(lead_id, stage_id):
        order.append("amo_stage")

    monkeypatch.setattr(
        "agent8_notary.service.process_confirmed_booking",
        _track_notary,
    )
    runtime.amo.update_lead_status.side_effect = _track_stage
    turn = make_turn(booking_confirmed=True)
    session, event, sender = _prepare_session(runtime, turn)

    asyncio.run(runtime.run(event, sender))

    assert order.index("notary") < order.index("amo_stage")
    runtime.amo.update_lead_status.assert_any_call(session.amo_lead_id, 22)


def test_notary_generation_failure_still_runs_handoff_and_save(runtime, monkeypatch):
    async def _sync_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def _fail_generate(session, contact):
        raise RuntimeError("doc fail")

    monkeypatch.setattr(
        "agent8_notary.service.process_confirmed_booking",
        process_confirmed_booking,
    )
    monkeypatch.setattr("agent8_notary.booking_doc.generate_booking_doc", _fail_generate)
    monkeypatch.setattr("agent8_notary.service.asyncio.to_thread", _sync_thread)

    turn = make_turn(booking_confirmed=True, handoff_to_human=True)
    session, event, sender = _prepare_session(runtime, turn)

    asyncio.run(runtime.run(event, sender))

    assert any(err[0] == "booking_doc" for err in runtime.notify_errors)
    assert any(
        "договор не сформирован — бронь зафиксирована" in err[2]
        for err in runtime.notify_errors
    )
    assert len(runtime.manager_calls) == 1
    assert len(runtime.store.saved) == 1


def test_handoff_to_human_calls_notify_manager(runtime):
    turn = make_turn(handoff_to_human=True)
    session, event, sender = _prepare_session(runtime, turn, text="Жду менеджера")

    asyncio.run(runtime.run(event, sender))

    assert len(runtime.manager_calls) == 1
    text, dedup_key = runtime.manager_calls[0]
    assert dedup_key == session.chat_id
    assert text == _expected_manager_message(session, sender, "Жду менеджера")
    assert "бюджет" not in text.lower()
    assert "budget" not in text.lower()


def test_session_handoff_triggers_notify_manager(runtime):
    turn = make_turn(handoff_to_human=False)
    session = make_client_session(handoff_to_human=True)
    runtime.store._by_chat[session.chat_id] = session
    import agent6_qualifier.tg_userbot as tg
    tg._sessions[session.chat_id] = session
    runtime.qualifier.handle_message.return_value = turn
    event = make_event(text="Ещё вопрос")
    sender = make_sender()
    event.get_sender.return_value = sender

    asyncio.run(runtime.run(event, sender))

    assert len(runtime.manager_calls) == 1
    text, dedup_key = runtime.manager_calls[0]
    assert dedup_key == session.chat_id
    assert text == _expected_manager_message(session, sender, "Ещё вопрос")


def test_handoff_false_skips_notify_manager(runtime):
    turn = make_turn(handoff_to_human=False)
    session = make_client_session(handoff_to_human=False)
    runtime.store._by_chat[session.chat_id] = session
    import agent6_qualifier.tg_userbot as tg
    tg._sessions[session.chat_id] = session
    runtime.qualifier.handle_message.return_value = turn
    event = make_event()
    sender = make_sender()
    event.get_sender.return_value = sender

    asyncio.run(runtime.run(event, sender))

    assert runtime.manager_calls == []


def test_runtime_order_before_save(runtime, monkeypatch):
    order: list[str] = []
    real_create_task = asyncio.create_task

    async def _track_humanized(event, reply):
        order.append("client_reply")

    async def _track_notary(session, contact, **kwargs):
        order.append("notary")
        return NotaryBookingResult()

    def _track_manager(text, *, dedup_key=""):
        order.append("manager")

    def _track_create_task(coro, **kwargs):
        if getattr(coro, "cr_code", None) and coro.cr_code.co_name == "_run_outreach":
            order.append("create_task")
        return real_create_task(coro, **kwargs)

    def _track_save(session):
        order.append("save")
        runtime.store.saved.append(session)

    monkeypatch.setattr("agent6_qualifier.tg_userbot.humanized_respond", _track_humanized)
    monkeypatch.setattr(
        "agent8_notary.service.process_confirmed_booking",
        _track_notary,
    )
    monkeypatch.setattr("agent6_qualifier.alerts.notify_manager", _track_manager)
    import agent6_qualifier.tg_userbot as tg
    monkeypatch.setattr(tg.asyncio, "create_task", _track_create_task)
    runtime.store.save = _track_save  # type: ignore[method-assign]

    turn = make_turn(
        booking_confirmed=True,
        handoff_to_human=True,
        need_owner_check=True,
    )
    session, event, sender = _prepare_session(runtime, turn)

    asyncio.run(runtime.run(event, sender))
    asyncio.run(runtime.drain_outreach_tasks())

    assert order.index("client_reply") < order.index("notary")
    assert order.index("notary") < order.index("manager")
    assert order.index("manager") < order.index("create_task")
    assert order.index("create_task") < order.index("save")
    runtime.assert_outreach_inflight_clear(session.chat_id)


def test_notify_manager_error_is_swallowed_by_outer_handler(runtime, monkeypatch):
    def _boom(text, *, dedup_key=""):
        raise RuntimeError("manager down")

    monkeypatch.setattr("agent6_qualifier.alerts.notify_manager", _boom)
    turn = make_turn(handoff_to_human=True)
    session, event, sender = _prepare_session(runtime, turn)

    asyncio.run(runtime.run(event, sender))

    assert any(err[0] == "agent7.handler" for err in runtime.notify_errors)
    assert runtime.store.saved == []


def test_save_error_after_outreach_task_still_clears_inflight(runtime):
    turn = make_turn(need_owner_check=True)
    session, event, sender = _prepare_session(runtime, turn)

    def _save_raises(sess):
        runtime.store.saved.append(sess)
        raise RuntimeError("disk full")

    runtime.store.save = _save_raises  # type: ignore[method-assign]

    asyncio.run(runtime.run(event, sender))

    assert any(err[0] == "agent7.handler" for err in runtime.notify_errors)
    asyncio.run(runtime.drain_outreach_tasks())
    runtime.assert_outreach_inflight_clear(session.chat_id)
