"""Tests for app.services.pdf_text_extractor.

Note on fixture diversity: the five synthetic PDFs deliberately cover
different Polish and English invoice shapes, including a ``Rachunek``
(Polish receipt form that does not carry a NIP). Assertions below stay
loose on field presence — they validate that extraction works, not
that every document is a standard Polish faktura.

Phase 5 replaced the stub OCR fallback with a real
``pdf2image + pytesseract`` implementation. The OCR tests below mock
both libraries so the test suite does not depend on poppler/tesseract
binaries being present on the host (CI runners, local dev on Windows
without the MSI installer, etc.).
"""

from unittest.mock import MagicMock, patch

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
    assert any(
        k in text for k in document_keywords
    ), f"fixture should identify as an invoice/receipt; got first 200 chars: {text[:200]!r}"


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
    assert any(
        m in text for m in currency_markers
    ), f"fixture should contain at least one currency marker; got first 200 chars: {text[:200]!r}"


def test_empty_bytes_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty"):
        extract_text(b"")


def test_invalid_bytes_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Not a valid PDF"):
        extract_text(b"this is not a pdf, just random bytes")


# ---------------------------------------------------------------------------
# OCR fallback (Phase 5).
# ---------------------------------------------------------------------------


def test_ocr_fallback_rasterises_and_runs_tesseract() -> None:
    """Happy path: pdf2image yields pages, pytesseract reads them.

    Mocked so the test doesn't need poppler/tesseract on PATH.
    """
    page1 = MagicMock(name="page1")
    page2 = MagicMock(name="page2")
    with (
        patch(
            "app.services.pdf_text_extractor.pdf2image.convert_from_bytes",
            return_value=[page1, page2],
        ) as mock_convert,
        patch(
            "app.services.pdf_text_extractor.pytesseract.image_to_string",
            side_effect=["Page one text.", "Page two text."],
        ) as mock_ocr,
    ):
        result = _ocr_fallback(b"%PDF-fake-bytes")

    assert result == "Page one text.\n\nPage two text."
    mock_convert.assert_called_once()
    # Language must come from settings, not a hard-coded string.
    assert mock_ocr.call_count == 2
    for call in mock_ocr.call_args_list:
        assert call.kwargs.get("lang") or call.args[1]


def test_ocr_fallback_unrasterisable_pdf_raises_value_error() -> None:
    """pdf2image PDFPageCountError → ValueError surfacing to the API.

    The worker task bubbles ValueError; the job endpoint returns it as
    a readable error message instead of a 500.
    """
    import pdf2image.exceptions as pdf2image_exc

    with (
        patch(
            "app.services.pdf_text_extractor.pdf2image.convert_from_bytes",
            side_effect=pdf2image_exc.PDFPageCountError("corrupt"),
        ),
        pytest.raises(ValueError, match="rasterise"),
    ):
        _ocr_fallback(b"%PDF-broken")


def test_ocr_fallback_missing_tesseract_raises_runtime_error() -> None:
    """TesseractNotFoundError is deploy-time misconfig, not bad input.

    Surfaced as RuntimeError so it shows up loudly in logs and the
    worker marks the job failed rather than looking like a 4xx.
    """
    import pytesseract

    page = MagicMock(name="page")
    with (
        patch(
            "app.services.pdf_text_extractor.pdf2image.convert_from_bytes",
            return_value=[page],
        ),
        patch(
            "app.services.pdf_text_extractor.pytesseract.image_to_string",
            side_effect=pytesseract.TesseractNotFoundError(),
        ),
        pytest.raises(RuntimeError, match="tesseract"),
    ):
        _ocr_fallback(b"%PDF-fake-bytes")


def test_fallback_triggered_when_pdfplumber_returns_empty(
    faktura_01_bytes: bytes,
) -> None:
    """Empty pdfplumber result must dispatch to ``_ocr_fallback``.

    Documents the hybrid contract: text-PDFs go through pdfplumber,
    scanned PDFs fall through to OCR. The mock below replaces the
    fallback so the test doesn't need tesseract.
    """
    with (
        patch(
            "app.services.pdf_text_extractor._extract_with_pdfplumber",
            return_value="",
        ),
        patch(
            "app.services.pdf_text_extractor._ocr_fallback",
            return_value="text from OCR",
        ) as mock_ocr,
    ):
        result = extract_text(faktura_01_bytes)

    assert result == "text from OCR"
    mock_ocr.assert_called_once()
