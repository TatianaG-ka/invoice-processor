"""FastAPI entrypoint for the invoice-processor service.

The POST ``/invoices`` endpoint runs the full Phase 2 pipeline:

1. Validate content-type and size.
2. Extract plain text from the PDF (``pdf_text_extractor``).
3. Run structured extraction over that text (``invoice_extractor``).
4. Return a JSON payload matching
   :class:`app.schemas.invoice.ExtractedInvoice`.

Image uploads (JPG/PNG) are deferred to Phase 5, which adds the OCR
leg and the async queue. For now they respond with 415.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile

from app.schemas.invoice import ExtractedInvoice
from app.services.invoice_extractor import InvoiceExtractionError, extract_invoice
from app.services.pdf_text_extractor import extract_text

app = FastAPI(
    title="Invoice Processor API",
    description=(
        "Automatic invoice processing: PDF → OCR → AI extraction → "
        "database entry + semantic search."
    ),
    version="0.2.0",
)

# Phase 2 accepts PDF only. image/jpeg and image/png re-open once the
# OCR fallback and queue land in Phase 5.
ALLOWED_CONTENT_TYPES = {"application/pdf"}
MAX_UPLOAD_SIZE_MB = 10
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024


@app.get("/", tags=["Health"])
def read_root():
    """Basic endpoint checking that the service is working."""
    return {"status": "ok", "service": "invoice-processor"}


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "healthy"}


@app.post(
    "/invoices",
    status_code=201,
    response_model=ExtractedInvoice,
    tags=["Invoices"],
)
async def upload_invoice(
    file: Annotated[UploadFile, File()],
) -> ExtractedInvoice:
    """Accept a PDF invoice and return structured extracted data."""
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type: {file.content_type}. "
                f"Accepted: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}. "
                "Image upload (JPG/PNG) arrives in Phase 5."
            ),
        )

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(contents) / 1024:.1f}KB > " f"{MAX_UPLOAD_SIZE_MB}MB",
        )

    try:
        text = extract_text(contents)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NotImplementedError as exc:
        # Scanned/image-only PDF — OCR path is Phase 5.
        raise HTTPException(
            status_code=501,
            detail="Scanned PDFs are not yet supported (OCR arrives in Phase 5).",
        ) from exc

    try:
        invoice = extract_invoice(text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvoiceExtractionError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream extraction failed: {exc}",
        ) from exc

    return invoice


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
