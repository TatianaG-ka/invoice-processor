"""Repository for :class:`~app.db.models.Invoice` rows.

The repository owns the mapping between the domain model
(:class:`~app.schemas.invoice.ExtractedInvoice`) and the ORM model in
both directions, so that routes and services stay domain-level and do
not import SQLAlchemy types.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invoice
from app.schemas.invoice import (
    ExtractedInvoice,
    LineItem,
    Party,
    StoredInvoice,
    Totals,
)

logger = logging.getLogger(__name__)


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
        # NIPs are personal data under GDPR and invoice counterparties
        # may be sensitive — log only the internal id and the invoice
        # number, never party NIPs or addresses.
        logger.info("Persisted invoice id=%d number=%r", row.id, row.invoice_number)
        return row

    async def get_by_id(self, invoice_id: int) -> Invoice | None:
        """Return the invoice with the given primary key, or ``None``."""
        return await self._session.get(Invoice, invoice_id)

    async def list_all(self, limit: int = 100) -> list[Invoice]:
        """Return up to ``limit`` invoices, newest first."""
        stmt = select(Invoice).order_by(Invoice.id.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def aggregate_by_category(
        self,
        period_days: int,
        currency: str,
    ) -> tuple[list[tuple[str | None, int, Decimal]], int, Decimal]:
        """Aggregate invoice totals grouped by category over a recent window.

        Returns ``(rows, total_count, grand_total)`` where ``rows`` is a
        list of ``(category, count, sum_total_gross)`` tuples, ordered
        by total descending. Filtering happens server-side via SQL
        ``GROUP BY`` — the response size is independent of the number
        of invoices in the window, which is the whole point.

        ``category`` may be ``None`` for invoices that have not been
        through ``POST /invoices/{id}/categorize`` yet; the
        un-categorised bucket is included rather than silently dropped.
        """
        cutoff = datetime.now(UTC) - timedelta(days=period_days)
        sum_expr = func.coalesce(func.sum(Invoice.total_gross), 0)
        stmt = (
            select(
                Invoice.category,
                func.count(Invoice.id),
                sum_expr,
            )
            .where(Invoice.created_at >= cutoff)
            .where(Invoice.currency == currency)
            .group_by(Invoice.category)
            .order_by(sum_expr.desc())
        )
        result = await self._session.execute(stmt)
        rows: list[tuple[str | None, int, Decimal]] = [
            (category, int(count), Decimal(str(total or 0)))
            for category, count, total in result.all()
        ]
        total_count = sum(row[1] for row in rows)
        grand_total = sum((row[2] for row in rows), Decimal("0"))
        return rows, total_count, grand_total

    async def update_category(
        self,
        invoice_id: int,
        category: str,
        confidence: float,
    ) -> Invoice | None:
        """Persist a categorization onto an existing invoice row.

        Returns the refreshed row, or ``None`` if the id does not
        exist (the route translates that to a 404). ``confidence`` is
        stored as ``Numeric(4, 3)`` — three decimals are plenty for
        an illustrative confidence; SQLAlchemy handles float→Decimal.
        """
        row = await self._session.get(Invoice, invoice_id)
        if row is None:
            return None
        row.category = category
        row.category_confidence = confidence
        await self._session.commit()
        await self._session.refresh(row)
        logger.info(
            "Categorized invoice id=%d as %r (confidence=%.3f)",
            invoice_id,
            category,
            confidence,
        )
        return row


def orm_to_stored_invoice(row: Invoice) -> StoredInvoice:
    """Build a :class:`StoredInvoice` from an :class:`Invoice` ORM row.

    Lives in the DB layer — this is ORM→schema mapping and should not
    leak SQLAlchemy types into the schema module.
    """
    return StoredInvoice(
        id=row.id,
        created_at=row.created_at,
        invoice_number=row.invoice_number,
        issue_date=row.issue_date,
        seller=Party(name=row.seller_name, nip=row.seller_nip, address=row.seller_address),
        buyer=Party(name=row.buyer_name, nip=row.buyer_nip, address=row.buyer_address),
        line_items=[LineItem(**item) for item in (row.line_items or [])],
        totals=Totals(
            net=row.total_net,
            vat=row.total_vat,
            gross=row.total_gross,
            currency=row.currency,
        ),
    )


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


def _line_item_to_json(line_item: LineItem) -> dict:
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
