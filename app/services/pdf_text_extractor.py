"""PDF text extraction service.

Extracts plain text from PDF bytes using a hybrid strategy:

1. **pdfplumber** as primary — pure-Python, fast, handles PDFs with an
   embedded text layer (digitally generated PDFs, most exports from
   accounting software).
2. **OCR fallback** (``pytesseract`` + ``pdf2image``) — used only when
   pdfplumber yields empty text, which signals a scanned / image-only
   PDF.

In Phase 1 the OCR fallback is a stub that raises ``NotImplementedError``.
Phase 5 swaps the stub body for a real implementation — the interface
above (same signature, ``bytes -> str``) is locked in now so Phase 5 is
a one-function swap, not a refactor.
"""

from __future__ import annotations

import io
import logging

import pdfplumber

logger = logging.getLogger(__name__)


def extract_text(pdf_bytes: bytes) -> str:
    """Return plain text extracted from a PDF.

    Strategy:
        1. Try pdfplumber — works for PDFs with an embedded text layer.
        2. If pdfplumber yields an empty string, fall back to OCR
           (currently a stub; real implementation arrives in Phase 5).

    Args:
        pdf_bytes: Raw bytes of the PDF file.

    Returns:
        Extracted text, stripped of leading/trailing whitespace.
        Guaranteed non-empty on success (empty result triggers OCR).

    Raises:
        ValueError: If ``pdf_bytes`` is empty or is not a valid PDF.
        NotImplementedError: If the PDF has no embedded text layer —
            the OCR fallback is a Phase 5 task.
    """
    if not pdf_bytes:
        raise ValueError("pdf_bytes is empty")

    text = _extract_with_pdfplumber(pdf_bytes)
    if text:
        return text

    logger.info(
        "pdfplumber returned empty text; falling back to OCR "
        "(stub in Phase 1, real impl in Phase 5)"
    )
    return _ocr_fallback(pdf_bytes)


def _extract_with_pdfplumber(pdf_bytes: bytes) -> str:
    """Extract text using pdfplumber.

    Returns an empty string when the PDF has no extractable text layer,
    which is the signal for :func:`extract_text` to trigger OCR.

    Args:
        pdf_bytes: Raw PDF bytes.

    Returns:
        Extracted text (may be empty for scanned PDFs).

    Raises:
        ValueError: If the bytes do not form a valid PDF.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = [page.extract_text() or "" for page in pdf.pages]
    except Exception as exc:  # pdfplumber surfaces several low-level errors
        raise ValueError(f"Not a valid PDF: {exc!r}") from exc

    return "\n\n".join(pages_text).strip()


def _ocr_fallback(pdf_bytes: bytes) -> str:  # noqa: ARG001
    """OCR fallback for scanned / image-only PDFs.

    Phase 1: stub that raises ``NotImplementedError``. The concrete
    implementation lands in Phase 5 and will use ``pytesseract`` +
    ``pdf2image`` (with ``OCR_LANGUAGES='pol+eng'`` from settings).
    The signature above keeps Phase 5 a one-function swap.

    Raises:
        NotImplementedError: Always, until Phase 5 swaps the body.
    """
    raise NotImplementedError(
        "OCR fallback not yet implemented. This path is reached when "
        "pdfplumber cannot extract text (scanned/image-only PDFs). "
        "Implementation target: Phase 5 (pytesseract + pdf2image)."
    )
