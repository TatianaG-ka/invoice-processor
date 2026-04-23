"""Repository-level tests for :class:`InvoiceRepository`.

These tests exercise the ORM mapping directly against the in-memory
SQLite engine (``db_session`` fixture in ``conftest.py``) so a failure
in the persistence layer is isolated from endpoint-level concerns.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.invoice_repository import InvoiceRepository
from app.schemas.invoice import ExtractedInvoice, LineItem, Party, Totals


def _make_invoice(number: str = "FV-001") -> ExtractedInvoice:
    return ExtractedInvoice(
        invoice_number=number,
        issue_date=date(2026, 1, 15),
        seller=Party(name="Acme Sp. z o.o.", nip="0000000000", address="ul. Przykładowa 1"),
        buyer=Party(name="Kontrahent S.A.", nip="1111111111", address="ul. Kliencka 2"),
        line_items=[
            LineItem(
                description="Usługa konsultingowa",
                quantity=Decimal("1"),
                unit_price=Decimal("1000.00"),
                total=Decimal("1000.00"),
            )
        ],
        totals=Totals(
            net=Decimal("1000.00"),
            vat=Decimal("230.00"),
            gross=Decimal("1230.00"),
            currency="PLN",
        ),
    )


async def test_save_returns_row_with_id(db_session: AsyncSession):
    repo = InvoiceRepository(db_session)
    saved = await repo.save(_make_invoice())

    assert saved.id is not None
    assert saved.invoice_number == "FV-001"
    assert saved.seller_name == "Acme Sp. z o.o."
    assert saved.buyer_nip == "1111111111"
    assert saved.total_gross == Decimal("1230.00")
    assert saved.currency == "PLN"
    assert saved.created_at is not None


async def test_save_persists_line_items_as_json(db_session: AsyncSession):
    repo = InvoiceRepository(db_session)
    saved = await repo.save(_make_invoice())

    assert isinstance(saved.line_items, list)
    assert len(saved.line_items) == 1
    item = saved.line_items[0]
    assert item["description"] == "Usługa konsultingowa"
    # Precision-preserving string representation.
    assert item["total"] == "1000.00"


async def test_get_by_id_roundtrip(db_session: AsyncSession):
    repo = InvoiceRepository(db_session)
    saved = await repo.save(_make_invoice("FV-002"))

    fetched = await repo.get_by_id(saved.id)
    assert fetched is not None
    assert fetched.id == saved.id
    assert fetched.invoice_number == "FV-002"


async def test_get_by_id_missing_returns_none(db_session: AsyncSession):
    repo = InvoiceRepository(db_session)
    assert await repo.get_by_id(999_999) is None


async def test_list_all_orders_by_id_desc(db_session: AsyncSession):
    repo = InvoiceRepository(db_session)
    first = await repo.save(_make_invoice("FV-010"))
    second = await repo.save(_make_invoice("FV-011"))
    third = await repo.save(_make_invoice("FV-012"))

    listed = await repo.list_all()
    assert [inv.id for inv in listed] == [third.id, second.id, first.id]


async def test_list_all_respects_limit(db_session: AsyncSession):
    repo = InvoiceRepository(db_session)
    for i in range(5):
        await repo.save(_make_invoice(f"FV-{i:03d}"))

    listed = await repo.list_all(limit=2)
    assert len(listed) == 2


async def test_save_handles_optional_fields_as_null(db_session: AsyncSession):
    """``invoice_number``, ``issue_date`` and NIPs may all be missing."""
    invoice = ExtractedInvoice(
        invoice_number=None,
        issue_date=None,
        seller=Party(name="Anon seller", nip=None, address=None),
        buyer=Party(name="Anon buyer", nip=None, address=None),
        line_items=[],
        totals=Totals(
            net=Decimal("0.00"),
            vat=Decimal("0.00"),
            gross=Decimal("0.00"),
            currency="PLN",
        ),
    )
    repo = InvoiceRepository(db_session)
    saved = await repo.save(invoice)

    assert saved.id is not None
    assert saved.invoice_number is None
    assert saved.issue_date is None
    assert saved.seller_nip is None
    assert saved.line_items == []


@pytest.mark.parametrize(
    "quantity,unit,total",
    [
        (Decimal("2"), Decimal("99.99"), Decimal("199.98")),
        (Decimal("1.5"), Decimal("1234.56"), Decimal("1851.84")),
    ],
)
async def test_line_item_decimals_are_stringified(db_session: AsyncSession, quantity, unit, total):
    """Money fields survive the JSON round-trip with full precision."""
    invoice = ExtractedInvoice(
        invoice_number="FV-DEC",
        issue_date=None,
        seller=Party(name="S", nip=None),
        buyer=Party(name="B", nip=None),
        line_items=[
            LineItem(
                description="x",
                quantity=quantity,
                unit_price=unit,
                total=total,
            )
        ],
        totals=Totals(
            net=total,
            vat=Decimal("0.00"),
            gross=total,
            currency="PLN",
        ),
    )
    repo = InvoiceRepository(db_session)
    saved = await repo.save(invoice)

    stored = saved.line_items[0]
    assert Decimal(stored["quantity"]) == quantity
    assert Decimal(stored["unit_price"]) == unit
    assert Decimal(stored["total"]) == total
