"""Agent 8 Notary: orchestration подтверждённой брони (docx + клиент + amo attach)."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent7.qualifier import Session

GenerateDoc = Callable[[Session, str], Path]
SendDoc = Callable[[Path], Awaitable[None]]
AttachFile = Callable[[int, Path], None]
ErrorHandler = Callable[[Exception], None]
RunInThread = Callable[..., Awaitable[Any]]


@dataclass
class NotaryBookingResult:
    doc_path: Path | None = None
    sent_to_client: bool = False
    attached_to_amo: bool = False


async def process_confirmed_booking(
    session: Session,
    client_contact: str,
    *,
    generate_doc: GenerateDoc,
    send_doc: SendDoc,
    amo_lead_id: int | None = None,
    attach_file: AttachFile | None = None,
    on_generation_error: ErrorHandler,
    on_attach_error: ErrorHandler,
    run_in_thread: RunInThread = asyncio.to_thread,
) -> NotaryBookingResult:
    """Сгенерировать docx, отправить клиенту и прикрепить к текущей сделке amo.

    Не создаёт новую сделку и не меняет стадии amo — только документ и attach.

    on_generation_error сохраняет legacy error flow: вызывается и при ошибке
    генерации документа, и при ошибке отправки документа клиенту. При ошибке
    отправки уже созданный документ всё равно может быть прикреплён к amoCRM.
    """
    result = NotaryBookingResult()
    doc_path: Path | None = None
    try:
        path = await run_in_thread(generate_doc, session, client_contact)
        if not path.exists():
            raise FileNotFoundError(f"документ не создан: {path}")
        doc_path = path
        await send_doc(doc_path)
        result.sent_to_client = True
    except Exception as exc:
        on_generation_error(exc)

    result.doc_path = doc_path

    if doc_path is not None and attach_file is not None and amo_lead_id is not None:
        try:
            await run_in_thread(attach_file, amo_lead_id, doc_path)
            result.attached_to_amo = True
        except Exception as exc:
            on_attach_error(exc)

    return result
