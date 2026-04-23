"""Repository for :class:`~app.db.models.Invoice` rows.

The repository owns the mapping between the domain model
(:class:`~app.schemas.invoice.ExtractedInvoice`) and the ORM model,
so that routes and services stay domain-level and do not import
SQLAlchemy types.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invoice
from app.schemas.invoice import ExtractedInvoice


class InvoiceRepository:
    """Persistence helper for invoice rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, extracted: ExtractedInvoice) -> Invoice:
        """Persist ``extracted`` and return the materialised ORM row.

        Commits the session so the caller receives a row with a stable
        primary key; the session is flushed/committed but not closed
        (closure is the dependency's job).
        """
        row = _to_orm(extracted)
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, invoice_id: int) -> Invoice | None:
        """Return the invoice with the given primary key, or ``None``."""
        return await self._session.get(Invoice, invoice_id)

    async def list_all(self, limit: int = 100) -> list[Invoice]:
        """Return up to ``limit`` invoices, newest first."""
        stmt = select(Invoice).order_by(Invoice.id.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


def _to_orm(extracted: ExtractedInvoice) -> Invoice:
    """Flatten :class:`ExtractedInvoice` into the :class:`Invoice` row."""
    return Invoice(
        invoice_number=extracted.invoice_number,
        issue_date=extracted.issue_date,
        seller_name=extracted.seller.name,
        seller_nip=extracted.seller.nip,
        seller_address=extracted.seller.address,
        buyer_name=extracted.buyer.name,
        buyer_nip=extracted.buyer.nip,
        buyer_address=extracted.buyer.address,
        total_net=extracted.totals.net,
        total_vat=extracted.totals.vat,
        total_gross=extracted.totals.gross,
        currency=extracted.totals.currency,
        line_items=[_line_item_to_json(li) for li in extracted.line_items],
    )


def _line_item_to_json(line_item) -> dict:
    """Serialise a :class:`LineItem` for JSON storage.

    ``Decimal`` is not JSON-native; the column is JSON, so we stringify
    money fields to preserve precision on the way in and out. The DB
    driver (SQLAlchemy JSON type) handles round-trip through the
    underlying ``jsonb`` (Postgres) or ``json`` (SQLite) column.
    """
    return {
        "description": line_item.description,
        "quantity": _decimal_to_str(line_item.quantity),
        "unit_price": _decimal_to_str(line_item.unit_price),
        "total": _decimal_to_str(line_item.total),
    }


def _decimal_to_str(value: Decimal) -> str:
    """Preserve precision by stringifying; avoids float round-trip."""
    return format(value, "f")
