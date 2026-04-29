"""Aggregate response models for ``GET /invoices/stats``.

Pure analytical view — counts and totals grouped by ``category``. Lives
in its own module to keep :mod:`app.schemas.invoice` focused on the
core invoice shape and its extraction-related projections.

Money fields are serialised as strings (Pydantic v2 default for
:class:`decimal.Decimal`) to preserve precision on the wire — the same
convention every other monetary response uses.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class CategoryStats(BaseModel):
    """One row of the per-category aggregation.

    ``category`` is ``None`` for invoices that have not yet been
    categorised via ``POST /invoices/{id}/categorize`` — surfacing them
    explicitly is preferable to silently dropping the bucket, since the
    "uncategorised" share is itself a useful signal for the caller.
    """

    category: str | None
    count: int = Field(ge=0)
    total_gross: Decimal


class InvoiceStats(BaseModel):
    """Aggregated invoice totals over a recent window.

    Constant-size payload regardless of invoice count — the workhorse
    for n8n / Slack / email reports that would otherwise have to fetch
    every row and re-aggregate client-side.
    """

    period_days: int = Field(ge=1)
    currency: str
    total_invoices: int = Field(ge=0)
    grand_total_gross: Decimal
    by_category: list[CategoryStats]
