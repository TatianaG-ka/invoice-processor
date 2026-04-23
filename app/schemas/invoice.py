"""Invoice domain models.

Two layers of models live here:

* **Domain models** (:class:`ExtractedInvoice` and its parts) use
  :class:`decimal.Decimal` for every monetary field and
  :class:`datetime.date` for dates. These are the types the rest of the
  application (DB layer in Phase 3, KSeF parser in Phase 4, Qdrant
  indexer in Phase 6) consumes.
* **Wire models** (the ``_LLM*`` classes) mirror the domain structure
  but use primitive types (``float``, ``str``) because OpenAI's
  Structured Outputs strict mode does not accept ``Decimal``,
  ``datetime.date`` or default values. They are an internal detail of
  :mod:`app.services.invoice_extractor` and never leave it.

:func:`from_llm_response` bridges the two: LLM returns a wire model, we
materialise a strongly-typed domain model.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _to_decimal(value: object) -> Decimal:
    """Coerce int/float/str/Decimal to :class:`Decimal`.

    Strings with Polish-style formatting (``"1 234,56"``) are normalised
    before parsing. ``float`` is routed through ``str(value)`` to avoid
    binary-float noise (``Decimal(0.1)`` != ``Decimal("0.1")``).
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        # Guard: bool is a subclass of int; refuse to treat True as "1.00 PLN".
        raise TypeError("bool cannot be coerced to Decimal")
    if isinstance(value, int | float):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.strip().replace(" ", "").replace(" ", "")
        cleaned = cleaned.replace(",", ".")
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"Not a decimal literal: {value!r}") from exc
    raise TypeError(f"Cannot coerce {type(value).__name__} to Decimal")


Money = Annotated[Decimal, BeforeValidator(_to_decimal)]


class Party(BaseModel):
    """Seller or buyer of an invoice."""

    name: str
    nip: str | None = None
    address: str | None = None


class LineItem(BaseModel):
    """A single line item from the invoice body."""

    description: str
    quantity: Money
    unit_price: Money
    total: Money


class Totals(BaseModel):
    """Invoice totals (net / VAT / gross)."""

    net: Money
    vat: Money
    gross: Money
    currency: str = "PLN"


class ExtractedInvoice(BaseModel):
    """Structured invoice data extracted from a source document.

    This is the target schema for every extractor — LLM-based
    (Phase 2), KSeF XML parser (Phase 4), OCR-driven (Phase 5). All
    downstream consumers depend on this shape.
    """

    model_config = ConfigDict(
        json_encoders={Decimal: lambda v: float(v)},
    )

    invoice_number: str | None = None
    issue_date: date | None = None
    seller: Party
    buyer: Party
    line_items: list[LineItem] = Field(default_factory=list)
    totals: Totals


# ---------------------------------------------------------------------------
# Wire models — LLM-facing, primitive types only.
# ---------------------------------------------------------------------------
#
# OpenAI Structured Outputs (strict mode) constraints we accommodate here:
#   * No ``Decimal`` — we use ``float`` and convert after the fact.
#   * No ``datetime.date`` — we use ``str`` in ISO-8601 (``YYYY-MM-DD``).
#   * No default values — every field must be required, nullability
#     expressed via ``X | None``. Empty strings/lists stand in for
#     "unknown" where None would imply a distinct semantic.
#
# These models are intentionally private to the extractor module.


class _LLMParty(BaseModel):
    name: str
    nip: str | None
    address: str | None


class _LLMLineItem(BaseModel):
    description: str
    quantity: float
    unit_price: float
    total: float


class _LLMTotals(BaseModel):
    net: float
    vat: float
    gross: float
    currency: str


class LLMInvoiceResponse(BaseModel):
    """Wire-format invoice payload returned by the LLM.

    Public (non-underscored) because test code needs to construct
    instances of it to exercise :func:`from_llm_response` without
    calling OpenAI.
    """

    invoice_number: str | None
    issue_date: str | None
    seller: _LLMParty
    buyer: _LLMParty
    line_items: list[_LLMLineItem]
    totals: _LLMTotals


def from_llm_response(payload: LLMInvoiceResponse) -> ExtractedInvoice:
    """Convert a wire-format LLM response into a domain model.

    Float money values become :class:`Decimal`; ISO-8601 strings become
    :class:`datetime.date`. An empty or malformed date string maps to
    ``None`` rather than raising — the LLM sometimes returns
    ``"nieznana"`` for missing data.
    """
    issue: date | None = None
    if payload.issue_date:
        try:
            issue = date.fromisoformat(payload.issue_date)
        except ValueError:
            issue = None

    return ExtractedInvoice(
        invoice_number=payload.invoice_number or None,
        issue_date=issue,
        seller=Party(**payload.seller.model_dump()),
        buyer=Party(**payload.buyer.model_dump()),
        line_items=[LineItem(**item.model_dump()) for item in payload.line_items],
        totals=Totals(**payload.totals.model_dump()),
    )
