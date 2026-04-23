"""Unit tests for :mod:`app.queue.tasks`.

The task function lives outside FastAPI — it's what the RQ worker
executes. These tests invoke it directly (not via the API) to pin
down its contract independently of the HTTP layer.

Both the conftest DB override and the force_mock_extractor fixture
below keep the test hermetic: no real Postgres, no real OpenAI.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invoice
from app.db.repositories.invoice_repository import InvoiceRepository
from app.queue.tasks import process_pdf_invoice
from app.services import invoice_extractor


@pytest.fixture(autouse=True)
def force_mock_extractor(monkeypatch):
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "mock")


@pytest_asyncio.fixture
async def fresh_session(test_session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as session:
        yield session


def test_process_pdf_invoice_returns_invoice_id(
    faktura_01_bytes: bytes,
) -> None:
    invoice_id = process_pdf_invoice(faktura_01_bytes, filename="faktura_01.pdf")
    assert isinstance(invoice_id, int)
    assert invoice_id > 0


@pytest.mark.asyncio
async def test_process_pdf_invoice_persists_mock_payload(
    faktura_01_bytes: bytes,
    fresh_session: AsyncSession,
) -> None:
    """After the task runs, the DB should contain the mock signal."""
    invoice_id = process_pdf_invoice(faktura_01_bytes)

    repo = InvoiceRepository(fresh_session)
    row = await repo.get_by_id(invoice_id)
    assert isinstance(row, Invoice)
    assert row.seller_name.startswith("MOCK")
    assert row.invoice_number == "MOCK/0001"


def test_process_pdf_invoice_rejects_invalid_pdf() -> None:
    """Bad bytes propagate as ValueError — RQ marks the job ``failed``."""
    with pytest.raises(ValueError, match="Not a valid PDF"):
        process_pdf_invoice(b"definitely not a pdf")


def test_process_pdf_invoice_rejects_empty_bytes() -> None:
    with pytest.raises(ValueError, match="empty"):
        process_pdf_invoice(b"")


def test_process_pdf_invoice_accepts_missing_filename(
    faktura_01_bytes: bytes,
) -> None:
    """``filename`` is optional — the task must not require it."""
    invoice_id = process_pdf_invoice(faktura_01_bytes)
    assert invoice_id > 0
