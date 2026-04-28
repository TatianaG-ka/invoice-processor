"""ORM models for the invoice-processor service.

The schema mirrors :class:`app.schemas.invoice.ExtractedInvoice` with
the usual relational concessions:

* ``seller`` / ``buyer`` / ``totals`` are flattened to columns rather
  than separate tables — Phase 3 keeps the footprint small, and nothing
  in the planned Phase 4-7 features requires party-level joins.
* ``line_items`` is stored as JSON rather than a one-to-many table.
  It is almost always read back whole with the parent invoice, and
  JSON keeps the repository code trivial. If a future query needs
  per-line aggregates, promoting to a child table is a straight
  migration.
* Money columns use ``Numeric(12, 2)`` — matches the Pydantic
  ``Decimal`` domain type and avoids binary-float drift.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Date, DateTime, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    """Python-side UTC timestamp for ``updated_at`` triggers.

    ``onupdate=func.now()`` works on Postgres (server-side) but SQLite has
    no native ``ON UPDATE`` DDL, so the trigger is silently dropped and
    the column stays pinned to the insert timestamp. A Python-side
    callable fires for every UPDATE regardless of backend, which is the
    behaviour every downstream flow (Phase 5 queue status writes,
    Phase 7 re-ingestion) actually expects.
    """
    return datetime.now(UTC)


class Invoice(Base):
    """A persisted invoice row."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    seller_name: Mapped[str] = mapped_column(String(255), nullable=False)
    seller_nip: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    seller_address: Mapped[str | None] = mapped_column(String(512), nullable=True)

    buyer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    buyer_nip: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    buyer_address: Mapped[str | None] = mapped_column(String(512), nullable=True)

    total_net: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_gross: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="PLN")

    line_items: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)

    # ADR-007: persisted LLM categorization. NULL until first
    # POST /invoices/{id}/categorize call. Re-categorization with
    # ?force=true overwrites in place.
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    category_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=_utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"Invoice(id={self.id!r}, invoice_number={self.invoice_number!r}, "
            f"seller_name={self.seller_name!r}, total_gross={self.total_gross!r})"
        )
