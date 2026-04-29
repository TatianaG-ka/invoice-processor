"""Invoice category enum + LLM wire/domain schemas.

Two-layer pattern mirroring :mod:`app.schemas.invoice`:

* :class:`InvoiceCategory` is a closed Enum of 12 business-friendly
  categories. Hard-coded rather than data-driven on purpose: a fixed
  set is easier for the LLM to anchor against, easier to test, and the
  list maps to the categories a Polish bookkeeper actually uses to
  bucket payables.
* :class:`LLMCategorizationResponse` is the wire model OpenAI
  Structured Outputs returns. Confidence as ``float`` is fine on the
  wire (no Decimal precision concern — confidence is illustrative,
  not financial).
* :class:`CategorizationResult` is the domain response model surfaced
  by the API. Adds ``invoice_id`` and ``cached`` so the caller can
  tell a fresh LLM call apart from a cached row read.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class InvoiceCategory(StrEnum):
    """Closed list of bookkeeping categories.

    String values double as the persisted column value (Postgres TEXT,
    SQLite TEXT) — the ``StrEnum`` base means ``InvoiceCategory.IT.value``
    is just ``"Usługi IT i oprogramowanie"`` and round-trips through the
    ORM without an explicit converter.
    """

    IT = "Usługi IT i oprogramowanie"
    CONSULTING = "Konsulting i doradztwo"
    MARKETING = "Marketing i reklama"
    TELECOM = "Telekomunikacja"
    UTILITIES = "Media (energia, woda, gaz)"
    TRANSPORT = "Transport i logistyka"
    RENT = "Najem i nieruchomości"
    OFFICE = "Materiały biurowe"
    TRAINING = "Szkolenia i edukacja"
    CATERING = "Catering i gastronomia"
    EQUIPMENT = "Sprzęt i wyposażenie"
    OTHER = "Inne"


class LLMCategorizationResponse(BaseModel):
    """Wire format for OpenAI Structured Outputs.

    Strict mode rejects defaults and certain types — keep this minimal
    and string-typed. The Enum is acceptable because OpenAI Structured
    Outputs supports JSON Schema ``enum``.
    """

    model_config = ConfigDict(extra="forbid")

    category: InvoiceCategory
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model's self-reported confidence in the assigned category, 0.0–1.0.",
    )
    reasoning: str = Field(
        description="One-sentence justification — why this category, in Polish.",
    )


class CategorizationResult(BaseModel):
    """API response for ``POST /invoices/{invoice_id}/categorize``.

    The ``cached`` flag tells the client whether this body comes from
    a fresh LLM call (False, returned with 201) or a previously-stored
    categorization (True, returned with 200). Mirrors the 201/200 flip
    used by ADR-006 idempotency on ``POST /invoices/ksef``.
    """

    model_config = ConfigDict(from_attributes=True)

    invoice_id: int
    category: InvoiceCategory
    confidence: float
    reasoning: str | None = None
    cached: bool = False
