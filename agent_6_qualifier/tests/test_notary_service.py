import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent7.qualifier import Session
from agent8.notary.service import process_confirmed_booking


def make_session(amo_lead_id: int | None = 77) -> Session:
    return Session(chat_id="1", amo_lead_id=amo_lead_id)


async def _run_in_thread(fn, *args):
    return fn(*args)


def test_success_generates_sends_and_attaches(tmp_path):
    doc = tmp_path / "booking.docx"
    doc.write_bytes(b"doc")
    session = make_session()
    gen_errors: list[Exception] = []
    attach_errors: list[Exception] = []
    send_doc = AsyncMock()
    attach_file = MagicMock()
    generate_doc = MagicMock(return_value=doc)

    result = asyncio.run(process_confirmed_booking(
        session,
        "Telegram: @client",
        generate_doc=generate_doc,
        send_doc=send_doc,
        amo_lead_id=session.amo_lead_id,
        attach_file=attach_file,
        on_generation_error=gen_errors.append,
        on_attach_error=attach_errors.append,
        run_in_thread=_run_in_thread,
    ))

    assert result.doc_path == doc
    assert result.sent_to_client is True
    assert result.attached_to_amo is True
    generate_doc.assert_called_once_with(session, "Telegram: @client")
    send_doc.assert_awaited_once_with(doc)
    attach_file.assert_called_once_with(77, doc)
    assert gen_errors == []
    assert attach_errors == []


def test_no_amo_client_skips_attach(tmp_path):
    doc = tmp_path / "booking.docx"
    doc.write_bytes(b"doc")
    send_doc = AsyncMock()

    result = asyncio.run(process_confirmed_booking(
        make_session(),
        "Telegram: @client",
        generate_doc=lambda s, contact: doc,
        send_doc=send_doc,
        amo_lead_id=77,
        attach_file=None,
        on_generation_error=lambda e: pytest.fail(f"unexpected generation error: {e}"),
        on_attach_error=lambda e: pytest.fail(f"unexpected attach error: {e}"),
        run_in_thread=_run_in_thread,
    ))

    assert result.sent_to_client is True
    assert result.attached_to_amo is False
    send_doc.assert_awaited_once()


def test_no_amo_lead_id_skips_attach(tmp_path):
    doc = tmp_path / "booking.docx"
    doc.write_bytes(b"doc")
    send_doc = AsyncMock()
    attach_file = MagicMock()

    result = asyncio.run(process_confirmed_booking(
        make_session(amo_lead_id=None),
        "Telegram: @client",
        generate_doc=lambda s, contact: doc,
        send_doc=send_doc,
        amo_lead_id=None,
        attach_file=attach_file,
        on_generation_error=lambda e: pytest.fail(f"unexpected generation error: {e}"),
        on_attach_error=lambda e: pytest.fail(f"unexpected attach error: {e}"),
        run_in_thread=_run_in_thread,
    ))

    assert result.sent_to_client is True
    assert result.attached_to_amo is False
    attach_file.assert_not_called()


def test_generation_error_skips_send_and_attach():
    gen_errors: list[Exception] = []
    send_doc = AsyncMock()
    attach_file = MagicMock()

    result = asyncio.run(process_confirmed_booking(
        make_session(),
        "Telegram: @client",
        generate_doc=lambda s, contact: (_ for _ in ()).throw(RuntimeError("boom")),
        send_doc=send_doc,
        amo_lead_id=77,
        attach_file=attach_file,
        on_generation_error=gen_errors.append,
        on_attach_error=lambda e: pytest.fail(f"unexpected attach error: {e}"),
        run_in_thread=_run_in_thread,
    ))

    assert result.doc_path is None
    assert result.sent_to_client is False
    assert result.attached_to_amo is False
    send_doc.assert_not_awaited()
    attach_file.assert_not_called()
    assert len(gen_errors) == 1
    assert str(gen_errors[0]) == "boom"


def test_missing_file_is_generation_error(tmp_path):
    missing = tmp_path / "missing.docx"
    gen_errors: list[Exception] = []
    send_doc = AsyncMock()
    attach_file = MagicMock()

    result = asyncio.run(process_confirmed_booking(
        make_session(),
        "Telegram: @client",
        generate_doc=lambda s, contact: missing,
        send_doc=send_doc,
        amo_lead_id=77,
        attach_file=attach_file,
        on_generation_error=gen_errors.append,
        on_attach_error=lambda e: pytest.fail(f"unexpected attach error: {e}"),
        run_in_thread=_run_in_thread,
    ))

    assert result.doc_path is None
    assert result.sent_to_client is False
    assert result.attached_to_amo is False
    send_doc.assert_not_awaited()
    attach_file.assert_not_called()
    assert len(gen_errors) == 1
    assert isinstance(gen_errors[0], FileNotFoundError)


def test_attach_error_does_not_undo_successful_send(tmp_path):
    doc = tmp_path / "booking.docx"
    doc.write_bytes(b"doc")
    gen_errors: list[Exception] = []
    attach_errors: list[Exception] = []
    send_doc = AsyncMock()

    def attach_file(lead_id, path):
        raise RuntimeError("amo down")

    result = asyncio.run(process_confirmed_booking(
        make_session(),
        "Telegram: @client",
        generate_doc=lambda s, contact: doc,
        send_doc=send_doc,
        amo_lead_id=77,
        attach_file=attach_file,
        on_generation_error=gen_errors.append,
        on_attach_error=attach_errors.append,
        run_in_thread=_run_in_thread,
    ))

    assert result.doc_path == doc
    assert result.sent_to_client is True
    assert result.attached_to_amo is False
    send_doc.assert_awaited_once_with(doc)
    assert gen_errors == []
    assert len(attach_errors) == 1
    assert str(attach_errors[0]) == "amo down"


def test_send_error_still_attempts_attach(tmp_path):
    """Как в tg_userbot: после сбоя send_file attach всё равно пробуется."""
    doc = tmp_path / "booking.docx"
    doc.write_bytes(b"doc")
    gen_errors: list[Exception] = []
    attach_errors: list[Exception] = []
    attach_file = MagicMock()

    async def failing_send(path):
        raise RuntimeError("telegram send failed")

    result = asyncio.run(process_confirmed_booking(
        make_session(),
        "Telegram: @client",
        generate_doc=lambda s, contact: doc,
        send_doc=failing_send,
        amo_lead_id=77,
        attach_file=attach_file,
        on_generation_error=gen_errors.append,
        on_attach_error=attach_errors.append,
        run_in_thread=_run_in_thread,
    ))

    assert result.doc_path == doc
    assert result.sent_to_client is False
    assert result.attached_to_amo is True
    assert len(gen_errors) == 1
    assert str(gen_errors[0]) == "telegram send failed"
    attach_file.assert_called_once_with(77, doc)
