"""FastAPI entrypoint for the invoice-processor service.

Two ingestion routes land invoices in the database:

* ``POST /invoices`` — PDF upload; enqueues a background job that
  runs the text extraction → LLM → persist pipeline (Phase 5).
  Returns 202 with a ``job_id``; the client polls
  ``GET /invoices/jobs/{job_id}`` and finally fetches the stored
  record via ``GET /invoices/{id}``.
* ``POST /invoices/ksef`` — KSeF XML upload; deterministic XML parse
  (dual FA(2)/FA(3) schema) → persist (Phase 4). Stays synchronous
  because the parse is fast and the client already holds the data
  it needs to render a preview.

``GET /invoices/{id}`` fetches a previously-stored invoice regardless
of its ingestion path.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import create_all
from app.db.repositories.invoice_repository import (
    InvoiceRepository,
    orm_to_stored_invoice,
)
from app.db.session import get_db
from app.queue.connection import queue_dependency
from app.queue.tasks import process_pdf_invoice
from app.schemas.invoice import StoredInvoice
from app.schemas.job import JobAccepted, JobStatus
from app.services.ksef_parser import KSeFParseError, parse_ksef


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
    version="0.5.0",
    lifespan=lifespan,
)

# PDFs are the only upload type on POST /invoices. Scanned PDFs go
# through the OCR fallback inside the worker; JPG/PNG remain out of
# scope (client-side rasterise into a single-page PDF if needed).
ALLOWED_CONTENT_TYPES = {"application/pdf"}
# Phase 4 accepts XML on POST /invoices/ksef. ``text/xml`` is the
# legacy MIME type still emitted by some tooling; both are valid.
KSEF_CONTENT_TYPES = {"application/xml", "text/xml"}
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
    status_code=202,
    response_model=JobAccepted,
    tags=["Invoices"],
)
def upload_invoice(
    file: Annotated[UploadFile, File()],
    queue: Annotated[Queue, Depends(queue_dependency)],
) -> JobAccepted:
    """Accept a PDF upload and enqueue background processing.

    Synchronous route (``def``) on purpose — the handler does no async
    work. Reading the body is sync, enqueueing is sync, and running
    the route in FastAPI's threadpool keeps ``asyncio.run`` inside the
    task function safe from nested-event-loop errors when the queue is
    configured to execute jobs inline (tests).
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type: {file.content_type}. "
                f"Accepted: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}. "
                "Rasterise images into a single-page PDF before upload."
            ),
        )

    contents = file.file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="Empty file upload.")
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(contents) / 1024:.1f}KB > {MAX_UPLOAD_SIZE_MB}MB",
        )

    job = queue.enqueue(process_pdf_invoice, contents, file.filename)
    return JobAccepted(
        job_id=job.id,
        status=job.get_status(),
        status_url=f"/invoices/jobs/{job.id}",
    )


@app.get(
    "/invoices/jobs/{job_id}",
    response_model=JobStatus,
    tags=["Invoices"],
)
def get_job_status(
    job_id: str,
    queue: Annotated[Queue, Depends(queue_dependency)],
) -> JobStatus:
    """Return the status of a previously-enqueued extraction job.

    Success path: ``status == "finished"`` + ``invoice_id`` set.
    The client then fetches ``GET /invoices/{invoice_id}``.

    Failure path: ``status == "failed"`` + ``error`` populated with
    the last line of the worker-side traceback (no full stack trace
    leaked to the client).
    """
    try:
        job = Job.fetch(job_id, connection=queue.connection)
    except NoSuchJobError as exc:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found") from exc

    status = job.get_status()
    invoice_id: int | None = None
    error: str | None = None

    if job.is_finished:
        result = job.return_value()
        if isinstance(result, int):
            invoice_id = result
    elif job.is_failed:
        error = _summarise_job_exception(job)

    return JobStatus(
        job_id=job.id,
        status=status,
        invoice_id=invoice_id,
        error=error,
    )


@app.post(
    "/invoices/ksef",
    status_code=201,
    response_model=StoredInvoice,
    tags=["Invoices"],
)
async def upload_ksef_invoice(
    file: Annotated[UploadFile, File()],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StoredInvoice:
    """Accept a KSeF XML invoice (FA(2) or FA(3)), persist, return stored."""
    if file.content_type not in KSEF_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type: {file.content_type}. "
                f"Accepted: {', '.join(sorted(KSEF_CONTENT_TYPES))}."
            ),
        )

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(contents) / 1024:.1f}KB > {MAX_UPLOAD_SIZE_MB}MB",
        )

    try:
        invoice = parse_ksef(contents)
    except KSeFParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    repo = InvoiceRepository(session)
    saved = await repo.save(invoice)
    return orm_to_stored_invoice(saved)


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
    return orm_to_stored_invoice(row)


def _summarise_job_exception(job: Job) -> str:
    """Extract a one-line message from a failed job's traceback.

    Uses ``job.latest_result()`` (the RQ 2.x API) and returns the last
    non-empty line of the traceback — the ``ExceptionType: message``
    pair, which is the only thing safe to surface to clients. Falls
    back to a generic message when no result is recorded (defensive —
    RQ normally always captures this on failure).
    """
    result = job.latest_result()
    exc_string = "" if result is None else (result.exc_string or "")
    for line in reversed(exc_string.strip().splitlines()):
        line = line.strip()
        if line:
            return line
    return "Job failed without a recorded exception."


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
