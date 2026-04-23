"""FastAPI entrypoint for the invoice-processor service.

The POST ``/invoices`` endpoint runs the full Phase 2 + Phase 3
pipeline:

1. Validate content-type and size.
2. Extract plain text from the PDF (``pdf_text_extractor``).
3. Run structured extraction over that text (``invoice_extractor``).
4. Persist the extracted invoice to the database.
5. Return a JSON payload matching
   :class:`app.schemas.invoice.StoredInvoice`.

``GET /invoices/{id}`` fetches a previously-stored invoice.

Image uploads (JPG/PNG) are deferred to Phase 5, which adds the OCR
leg and the async queue. For now they respond with 415.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import create_all
from app.db.repositories.invoice_repository import InvoiceRepository
from app.db.session import get_db
from app.schemas.invoice import StoredInvoice, stored_from_orm
from app.services.invoice_extractor import InvoiceExtractionError, extract_invoice
from app.services.pdf_text_extractor import extract_text


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup; no shutdown work beyond engine disposal."""
    await create_all()
    yield


app = FastAPI(
    title="Invoice Processor API",
    description=(
        "Automatic invoice processing: PDF → OCR → AI extraction → "
        "database entry + semantic search."
    ),
    version="0.3.0",
    lifespan=lifespan,
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
    response_model=StoredInvoice,
    tags=["Invoices"],
)
async def upload_invoice(
    file: Annotated[UploadFile, File()],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StoredInvoice:
    """Accept a PDF invoice, persist it, return the stored record."""
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

    repo = InvoiceRepository(session)
    saved = await repo.save(invoice)
    return stored_from_orm(saved)


@app.get(
    "/invoices/{invoice_id}",
    response_model=StoredInvoice,
    tags=["Invoices"],
)
async def get_invoice(
    invoice_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StoredInvoice:
    """Return the stored invoice with the given primary key."""
    repo = InvoiceRepository(session)
    row = await repo.get_by_id(invoice_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
    return stored_from_orm(row)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
