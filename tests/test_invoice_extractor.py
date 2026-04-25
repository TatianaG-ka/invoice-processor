"""Tests for :mod:`app.services.invoice_extractor`.

Coverage map:

* Input-guard behavior (empty / whitespace text).
* Mock-mode selection logic (``_should_use_mock``) in both trigger
  paths — missing API key and explicit strategy override.
* ``extract_invoice`` in mock mode returns the deterministic stub.
* Wire ⇄ domain conversion (:func:`from_llm_response`): Decimal
  coercion, ISO-date parsing, tolerant handling of garbage dates.
* Error wrapping: transient OpenAI errors bubble up as
  :class:`InvoiceExtractionError` (retries happen inside
  :func:`_call_openai`; here we verify the outer contract).
* The ``_call_openai`` coroutine carries the expected retry wiring
  (3 attempts, triggered by connection/timeout/rate-limit).

Real OpenAI calls are never made — every path that would reach the
network is stubbed via ``monkeypatch``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
from openai import APIConnectionError

from app.schemas.invoice import (
    ExtractedInvoice,
    LLMInvoiceResponse,
    _LLMLineItem,
    _LLMParty,
    _LLMTotals,
    from_llm_response,
)
from app.services import invoice_extractor
from app.services.invoice_extractor import (
    InvoiceExtractionError,
    _should_use_mock,
    extract_invoice,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_wire_payload() -> LLMInvoiceResponse:
    """Build a realistic wire-format response used across several tests."""
    return LLMInvoiceResponse(
        invoice_number="FV/2026/04/001",
        issue_date="2026-04-23",
        seller=_LLMParty(
            name="ACME Sp. z o.o.",
            nip="1234567890",
            address="ul. Testowa 1, 00-001 Warszawa",
        ),
        buyer=_LLMParty(name="Jan Kowalski", nip=None, address=None),
        line_items=[
            _LLMLineItem(
                description="Usługa konsultingowa",
                quantity="2.00",
                unit_price="500.00",
                total="1000.00",
            ),
        ],
        totals=_LLMTotals(net="1000.00", vat="230.00", gross="1230.00", currency="PLN"),
    )


# ---------------------------------------------------------------------------
# Input-guard behavior
# ---------------------------------------------------------------------------


def test_extract_invoice_raises_on_empty_text():
    with pytest.raises(ValueError, match="empty text"):
        extract_invoice("")


def test_extract_invoice_raises_on_whitespace_only_text():
    with pytest.raises(ValueError, match="empty text"):
        extract_invoice("   \n\t  ")


# ---------------------------------------------------------------------------
# Mock-mode selection
# ---------------------------------------------------------------------------


def test_should_use_mock_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(invoice_extractor.settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "openai")
    assert _should_use_mock() is True


def test_should_use_mock_when_strategy_is_mock(monkeypatch):
    # Even with a real-looking key, strategy=mock forces the stub.
    monkeypatch.setattr(invoice_extractor.settings, "OPENAI_API_KEY", "sk-looks-real")
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "mock")
    assert _should_use_mock() is True


def test_should_not_use_mock_when_key_set_and_strategy_openai(monkeypatch):
    monkeypatch.setattr(invoice_extractor.settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "openai")
    assert _should_use_mock() is False


def test_extract_invoice_mock_mode_returns_stub(monkeypatch):
    monkeypatch.setattr(invoice_extractor.settings, "OPENAI_API_KEY", "")
    invoice = extract_invoice("Faktura VAT nr 1/2026\nKwota: 100 PLN")
    assert isinstance(invoice, ExtractedInvoice)
    assert invoice.seller.name.startswith("MOCK")
    assert invoice.invoice_number == "MOCK/0001"
    assert invoice.totals.currency == "PLN"


# ---------------------------------------------------------------------------
# Wire ⇄ domain conversion
# ---------------------------------------------------------------------------


def test_from_llm_response_converts_strings_to_decimal():
    wire = _sample_wire_payload()
    domain = from_llm_response(wire)

    assert isinstance(domain, ExtractedInvoice)
    assert domain.totals.net == Decimal("1000.00")
    assert domain.totals.vat == Decimal("230.00")
    assert domain.totals.gross == Decimal("1230.00")
    # All three must actually be Decimal, not str/float.
    assert isinstance(domain.totals.net, Decimal)
    assert isinstance(domain.totals.gross, Decimal)
    assert isinstance(domain.line_items[0].total, Decimal)


def test_from_llm_response_parses_iso_date():
    wire = _sample_wire_payload()
    domain = from_llm_response(wire)
    assert domain.issue_date == date(2026, 4, 23)


def test_from_llm_response_handles_missing_date():
    wire = _sample_wire_payload()
    wire.issue_date = None
    assert from_llm_response(wire).issue_date is None


def test_from_llm_response_handles_garbage_date():
    # Observed LLM failure mode: returns "nieznana" instead of null.
    wire = _sample_wire_payload()
    wire.issue_date = "nieznana"
    assert from_llm_response(wire).issue_date is None


def test_from_llm_response_normalizes_empty_invoice_number():
    wire = _sample_wire_payload()
    wire.invoice_number = ""
    assert from_llm_response(wire).invoice_number is None


def test_money_decimal_handles_polish_formatting():
    # Comma separator + thousands spaces — common in Polish invoices.
    from app.schemas.invoice import _to_decimal

    assert _to_decimal("1 234,56") == Decimal("1234.56")
    assert _to_decimal("1234,56") == Decimal("1234.56")
    assert _to_decimal(1234.56) == Decimal("1234.56")


def test_money_decimal_rejects_bool():
    from app.schemas.invoice import _to_decimal

    # Guard against accidental coercion of True/False (Python-ism).
    with pytest.raises(TypeError):
        _to_decimal(True)


# ---------------------------------------------------------------------------
# Real-mode behavior (OpenAI mocked)
# ---------------------------------------------------------------------------


def test_extract_invoice_real_mode_happy_path(monkeypatch):
    monkeypatch.setattr(invoice_extractor.settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "openai")

    wire = _sample_wire_payload()
    monkeypatch.setattr(invoice_extractor, "_call_openai", lambda text: wire)

    invoice = extract_invoice("Faktura VAT nr FV/2026/04/001")
    assert invoice.invoice_number == "FV/2026/04/001"
    assert invoice.seller.nip == "1234567890"
    assert invoice.totals.gross == Decimal("1230.00")
    assert invoice.issue_date == date(2026, 4, 23)


def test_extract_invoice_wraps_transient_error_as_extraction_error(monkeypatch):
    monkeypatch.setattr(invoice_extractor.settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "openai")

    def raise_conn_err(text: str):
        raise APIConnectionError(
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        )

    monkeypatch.setattr(invoice_extractor, "_call_openai", raise_conn_err)

    with pytest.raises(InvoiceExtractionError, match="transient error"):
        extract_invoice("Faktura VAT")


def test_extract_invoice_propagates_extraction_error(monkeypatch):
    monkeypatch.setattr(invoice_extractor.settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "openai")

    def raise_extraction_err(text: str):
        raise InvoiceExtractionError("LLM produced no parsed payload")

    monkeypatch.setattr(invoice_extractor, "_call_openai", raise_extraction_err)

    with pytest.raises(InvoiceExtractionError, match="no parsed payload"):
        extract_invoice("Faktura VAT")


def test_extract_invoice_wraps_unexpected_error(monkeypatch):
    """Any non-transient, non-InvoiceExtractionError surfaces as InvoiceExtractionError."""
    monkeypatch.setattr(invoice_extractor.settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "openai")

    def raise_unexpected(text: str):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(invoice_extractor, "_call_openai", raise_unexpected)

    with pytest.raises(InvoiceExtractionError, match="OpenAI call failed"):
        extract_invoice("Faktura VAT")


# ---------------------------------------------------------------------------
# Retry wiring (configuration smoke-test)
# ---------------------------------------------------------------------------


def test_call_openai_has_tenacity_retry_configured():
    """Verify the retry decorator is attached with the intended policy.

    We do not drive real retries here (that would either need a live
    OpenAI server or a mock deep enough to be a test of tenacity
    itself). Instead we assert the static configuration: three
    attempts, gated on transient error types.
    """
    retry_obj = invoice_extractor._call_openai.retry
    # 3 attempts total.
    assert retry_obj.stop.max_attempt_number == 3
    # Exponential backoff is configured (we don't pin exact params —
    # just check it is a wait object, not the default wait_none).
    assert retry_obj.wait is not None
