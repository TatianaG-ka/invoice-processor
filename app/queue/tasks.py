"""Job functions executed by RQ workers.

Each top-level function here is a unit of background work. Workers
find these by their fully-qualified import path, so renaming or moving
them is a breaking change for any job already enqueued in Redis.

Workers are synchronous processes (``rq worker``), but the pipeline
they call is async (SQLAlchemy + aiosqlite/asyncpg). The bridge is
:func:`asyncio.run` — a fresh event loop per job keeps the worker
process stateless between jobs, which matches the RQ model.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Awaitable
from typing import TypeVar

from app.db.base import get_sessionmaker
from app.db.repositories.invoice_repository import InvoiceRepository
from app.schemas.invoice import ExtractedInvoice
from app.services.invoice_extractor import extract_invoice
from app.services.pdf_text_extractor import extract_text

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def _run_coroutine_blocking(coro: Awaitable[_T]) -> _T:
    """Run an async coroutine from sync code, nested-loop safe.

    RQ workers execute jobs in plain sync code, so the usual
    ``asyncio.run`` works. But the same task can also be invoked from
    an already-running event loop (async tests, inline queue mode
    running under FastAPI), where ``asyncio.run`` would raise
    ``RuntimeError: asyncio.run() cannot be called from a running event
    loop``. Detect the surrounding loop and offload to a fresh thread
    in that case — each thread owns its own event loop, so the nested
    constraint no longer applies.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)  # type: ignore[arg-type]
        return future.result()


def process_pdf_invoice(pdf_bytes: bytes, filename: str | None = None) -> int:
    """Run the full PDF → text → LLM → DB pipeline.

    Args:
        pdf_bytes: Raw PDF upload bytes (serialised into Redis by RQ).
        filename: Original upload filename; logged for traceability,
            otherwise unused. Optional so the task is callable from
            contexts without a filename (e.g. retries from scripts).

    Returns:
        The DB primary key of the persisted :class:`Invoice` row.
        Callers retrieve it via ``job.return_value()`` and follow up
        with ``GET /invoices/{id}`` to fetch the stored record.

    Raises:
        ValueError: On invalid PDF bytes or empty extractor text.
            RQ marks the job as ``failed`` and stores the traceback
            in ``job.exc_info`` — the status endpoint surfaces this
            back to the caller as a 4xx without leaking stack traces.
        Any other exception propagates and fails the job likewise.
    """
    logger.info(
        "process_pdf_invoice start filename=%r bytes=%d",
        filename,
        len(pdf_bytes),
    )
    text = extract_text(pdf_bytes)
    invoice = extract_invoice(text)
    invoice_id = _run_coroutine_blocking(_persist(invoice))
    logger.info("process_pdf_invoice done invoice_id=%d", invoice_id)
    return invoice_id


async def _persist(invoice: ExtractedInvoice) -> int:
    """Open a fresh async session and persist the extracted invoice.

    A worker process has no FastAPI dependency injection, so the task
    owns session lifetime. The session is closed on context exit;
    commits happen inside :meth:`InvoiceRepository.save`.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        repo = InvoiceRepository(session)
        row = await repo.save(invoice)
        return row.id
