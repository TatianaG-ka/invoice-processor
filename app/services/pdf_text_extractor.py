"""PDF text extraction service.

Extracts plain text from PDF bytes using a hybrid strategy:

1. **pdfplumber** as primary — pure-Python, fast, handles PDFs with an
   embedded text layer (digitally generated PDFs, most exports from
   accounting software).
2. **OCR fallback** (``pytesseract`` + ``pdf2image``) — used only when
   pdfplumber yields empty text, which signals a scanned / image-only
   PDF.

Phase 5 swapped the Phase 1 ``NotImplementedError`` stub for the real
OCR path. The public signature (``bytes -> str``) is unchanged so the
swap was a no-op for callers.
"""

from __future__ import annotations

import io
import logging

import pdf2image
import pdfplumber
import pytesseract

from app.config import settings

logger = logging.getLogger(__name__)

# DPI for the PDF→image rasterisation step. 200 DPI is the common
# tesseract sweet spot — below ~150 DPI accuracy drops sharply on small
# fonts; above ~300 DPI the per-page cost grows without extra signal.
_OCR_RASTER_DPI = 200


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

    logger.info("pdfplumber returned empty text; falling back to OCR")
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


def _ocr_fallback(pdf_bytes: bytes) -> str:
    """OCR-based text extraction for scanned / image-only PDFs.

    Steps:
        1. Rasterise each PDF page to an image via ``pdf2image``
           (which shells out to ``poppler`` / ``pdftoppm``).
        2. Run ``pytesseract`` per page with the configured
           languages (``settings.OCR_LANGUAGES``, defaults to
           ``"pol+eng"``).
        3. Concatenate page texts with a blank-line separator.

    Args:
        pdf_bytes: Raw PDF bytes.

    Returns:
        Extracted text, stripped.

    Raises:
        ValueError: If the PDF cannot be rasterised at all (corrupt
            bytes that survived pdfplumber but fail poppler).
        RuntimeError: If tesseract is not installed / not on PATH.
            Surfaced as-is so deploy-time misconfiguration is loud.
    """
    try:
        images = pdf2image.convert_from_bytes(pdf_bytes, dpi=_OCR_RASTER_DPI)
    except pdf2image.exceptions.PDFPageCountError as exc:
        raise ValueError(f"Cannot rasterise PDF for OCR: {exc!r}") from exc

    if not images:
        raise ValueError("PDF rasterised to zero pages; nothing to OCR")

    languages = settings.OCR_LANGUAGES
    pages_text: list[str] = []
    for page_no, img in enumerate(images, start=1):
        try:
            page_text = pytesseract.image_to_string(img, lang=languages)
        except pytesseract.TesseractNotFoundError as exc:
            # Deploy-time misconfiguration — don't swallow as ValueError,
            # a healthy runtime should always have tesseract on PATH.
            raise RuntimeError(
                "tesseract binary not found on PATH; install "
                "`tesseract-ocr` (+ language packs per settings.OCR_LANGUAGES) "
                "or check the Dockerfile."
            ) from exc
        pages_text.append(page_text.strip())
        logger.debug("OCR page %d/%d produced %d chars", page_no, len(images), len(page_text))

    return "\n\n".join(p for p in pages_text if p).strip()
