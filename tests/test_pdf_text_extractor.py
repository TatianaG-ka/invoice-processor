"""Tests for app.services.pdf_text_extractor.

Note on fixture diversity: the five synthetic PDFs deliberately cover
different Polish and English invoice shapes, including a ``Rachunek``
(Polish receipt form that does not carry a NIP). Assertions below stay
loose on field presence — they validate that extraction works, not
that every document is a standard Polish faktura.
"""

from unittest.mock import patch

import pytest

from app.services.pdf_text_extractor import (
    _ocr_fallback,
    extract_text,
)


def test_extracts_text_from_each_fixture(all_faktury_bytes: bytes) -> None:
    """Each synthetic fixture yields non-empty text via pdfplumber."""
    text = extract_text(all_faktury_bytes)
    assert text, "extract_text should return non-empty text for text-PDF fixtures"
    assert len(text) > 50, "extracted text should be meaningful length"


def test_extracted_text_has_document_marker(all_faktury_bytes: bytes) -> None:
    """Each fixture is recognisably an invoice-like document.

    Covers Polish faktura, Polish rachunek (receipt) and English invoice.
    At least one of the canonical document-type keywords must appear.
    """
    text = extract_text(all_faktury_bytes).lower()
    document_keywords = ["faktura", "rachunek", "invoice"]
    assert any(k in text for k in document_keywords), (
        f"fixture should identify as an invoice/receipt; " f"got first 200 chars: {text[:200]!r}"
    )


def test_extracted_text_has_tax_id_or_is_receipt(all_faktury_bytes: bytes) -> None:
    """Every fixture carries a tax-id marker, unless it's a minimal receipt.

    Polish faktury require NIP; English invoices use VAT; a minimal
    Polish ``rachunek`` (receipt form under simplified rules) may carry
    neither. Assertion permits any of these three shapes.
    """
    text = extract_text(all_faktury_bytes)
    tax_markers = ["NIP", "VAT", "REGON", "TIN"]
    has_marker = any(marker in text for marker in tax_markers)
    is_receipt = "rachunek" in text.lower()
    assert has_marker or is_receipt, (
        "fixture should contain a tax-id marker (NIP/VAT/REGON/TIN) "
        f"or be a Rachunek; got first 200 chars: {text[:200]!r}"
    )


def test_extracted_text_has_amount(all_faktury_bytes: bytes) -> None:
    """Each fixture contains at least one monetary amount with a currency.

    Relaxed check — just verifies that extraction preserves the parts
    of the document that matter for downstream field parsing.
    """
    text = extract_text(all_faktury_bytes).lower()
    currency_markers = ["pln", "zl", "zł", "eur", "usd", "gbp"]
    assert any(m in text for m in currency_markers), (
        "fixture should contain at least one currency marker; "
        f"got first 200 chars: {text[:200]!r}"
    )


def test_empty_bytes_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty"):
        extract_text(b"")


def test_invalid_bytes_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Not a valid PDF"):
        extract_text(b"this is not a pdf, just random bytes")


def test_ocr_fallback_stub_raises_not_implemented() -> None:
    """The Phase 1 OCR stub must be explicit about its Phase 5 target."""
    with pytest.raises(NotImplementedError, match="Phase 5"):
        _ocr_fallback(b"irrelevant")


def test_fallback_triggered_when_pdfplumber_returns_empty(
    faktura_01_bytes: bytes,
) -> None:
    """When pdfplumber yields empty text the OCR stub must be invoked.

    Documents the hybrid contract: empty pdfplumber result flows into
    ``_ocr_fallback``. Phase 5 replaces the stub body — this test will
    then assert the real OCR output instead of the NotImplementedError.
    """
    with (
        patch(
            "app.services.pdf_text_extractor._extract_with_pdfplumber",
            return_value="",
        ),
        pytest.raises(NotImplementedError, match="Phase 5"),
    ):
        extract_text(faktura_01_bytes)
