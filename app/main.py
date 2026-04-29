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

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import create_all
from app.db.repositories.invoice_repository import (
    InvoiceRepository,
    orm_to_stored_invoice,
)
from app.db.session import get_db
from app.queue.connection import queue_dependency
from app.queue.tasks import process_pdf_invoice
from app.schemas.category import CategorizationResult
from app.schemas.invoice import SearchHit, SearchResponse, StoredInvoice
from app.schemas.job import JobAccepted, JobStatus
from app.schemas.stats import CategoryStats, InvoiceStats
from app.services import embedder, idempotency
from app.services.invoice_categorizer import (
    InvoiceCategorizationError,
    InvoiceNotFoundError,
    categorize_invoice,
)
from app.services.ksef_parser import KSeFParseError, parse_ksef
from app.services.vector_store import (
    VectorStore,
    index_invoice,
    reindex_all,
    vector_store_dependency,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables, then warm the search index from Postgres.

    The embedded Qdrant store rides on Cloud Run's ephemeral filesystem,
    so every container boot needs to rebuild the index from the DB
    (which is durable on Neon). Reindex errors are non-fatal: a degraded
    search path must not keep the API from serving the rest of its
    surface, so we log and continue.
    """
    await create_all()
    try:
        await reindex_all()
    except Exception:  # noqa: BLE001 — startup must not die on search warmup
        logger.exception("Startup reindex failed; search will be empty until first write")
    yield


app = FastAPI(
    title="Invoice Processor API",
    description=(
        "**KSeF-compatible invoice intelligence service** — parse, store, search "
        "and categorize Polish invoices (FA(2) / FA(3)) with LLM-powered RAG.\n\n"
        "### Try it live (4 steps, ~30 seconds)\n"
        "1. **Download** a sample XML invoice: "
        "[fa3_minimal.xml](https://raw.githubusercontent.com/TatianaG-ka/invoice-processor/main/tests/fixtures/ksef/fa3_minimal.xml) "
        "(right-click → *Save link as…*)\n"
        "2. **Upload** it via `POST /invoices/ksef` (Try it out → Choose file → Execute) — "
        "returns `201 Created` with the stored invoice + assigned `id`.\n"
        "3. **Browse** all invoices with `GET /invoices` or fetch one by id "
        "with `GET /invoices/{id}`.\n"
        "4. **Categorize** with `POST /invoices/{id}/categorize` "
        "(LLM-driven, ~$0.0002/call, idempotent).\n\n"
        "Semantic search: try `GET /invoices/search?q=Acme` after step 2. "
        "Aggregate report: `GET /invoices/stats` (per-category totals, "
        "n8n-friendly).\n\n"
        "> ⚠️ **First request may return `503` (~10s cold start)** — this is hosted on "
        "Cloud Run with `min-instances=0` to keep the demo free. Retry once and it "
        "wakes up. Subsequent requests respond in milliseconds."
    ),
    version="0.6.0",
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


@app.get("/", tags=["Health"], include_in_schema=False)
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
    include_in_schema=False,  # PDF path requires a Redis worker (local dev only).
)
def upload_invoice(
    file: Annotated[UploadFile, File()],
    queue: Annotated[Queue, Depends(queue_dependency)],
) -> JobAccepted:
    """Accept a PDF upload and enqueue background processing (local dev only).

    Hidden from the public Swagger because Cloud Run runs without an
    RQ worker (cost optimization). Use ``POST /invoices/ksef`` for the
    deployed demo. The route stays wired for local development and the
    integration test suite, which runs the queue inline.
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
    include_in_schema=False,  # Companion to POST /invoices — local dev only.
)
def get_job_status(
    job_id: str,
    queue: Annotated[Queue, Depends(queue_dependency)],
) -> JobStatus:
    """Poll the status of a previously-enqueued PDF extraction job.

    Hidden from the public Swagger alongside ``POST /invoices`` —
    only meaningful when a Redis worker is running locally.
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
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StoredInvoice:
    """Upload a KSeF XML invoice (FA(2) or FA(3)) — parse, persist, index for search.

    **Idempotent**: re-posting the same invoice within 24h returns the
    original record with `200 OK` instead of creating a duplicate
    (matched on seller NIP + invoice number).

    **Try it now**: download the sample
    [fa3_minimal.xml](https://raw.githubusercontent.com/TatianaG-ka/invoice-processor/main/tests/fixtures/ksef/fa3_minimal.xml)
    and upload it via *Try it out → Choose file → Execute*.
    """
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

    # Idempotency: same (seller_nip, invoice_number) within 24h returns
    # the previously-stored row. Skipped when the parse did not yield
    # both fields (rare for KSeF — both are mandatory in FA(3) — but
    # defensive against partial FA(2) documents).
    dedup_key: str | None = None
    if invoice.seller.nip and invoice.invoice_number:
        dedup_key = idempotency.ksef_key(invoice.seller.nip, invoice.invoice_number)
        cached_id = await idempotency.find_existing(dedup_key)
        if cached_id is not None:
            try:
                cached_row = await repo.get_by_id(cached_id)
            except SQLAlchemyError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Database temporarily unavailable.",
                ) from exc
            if cached_row is not None:
                response.status_code = 200
                return orm_to_stored_invoice(cached_row)
            # Stale claim (row deleted out-of-band) — fall through to a
            # fresh save and overwrite the key below.

    try:
        saved = await repo.save(invoice)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable.") from exc

    if dedup_key is not None:
        await idempotency.claim(dedup_key, saved.id)
    # Best-effort indexing: a Qdrant outage must not block a successful
    # parse + persist. Same contract as the PDF/queue path.
    index_invoice(saved.id, invoice)
    return orm_to_stored_invoice(saved)


@app.get(
    "/invoices",
    response_model=list[StoredInvoice],
    tags=["Invoices"],
)
async def list_invoices(
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: int = 50,
) -> list[StoredInvoice]:
    """List recently stored invoices, newest first.

    Useful as an entry point: after uploading via ``POST /invoices/ksef``
    you can browse the table here without remembering the assigned id.
    Returns up to ``limit`` rows (default 50, max 100).
    """
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="'limit' must be between 1 and 100.")
    repo = InvoiceRepository(session)
    try:
        rows = await repo.list_all(limit=limit)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable.") from exc
    return [orm_to_stored_invoice(row) for row in rows]


@app.get(
    "/invoices/stats",
    response_model=InvoiceStats,
    tags=["Invoices"],
)
async def invoice_stats(
    session: Annotated[AsyncSession, Depends(get_db)],
    period_days: int = 30,
    currency: str = "PLN",
) -> InvoiceStats:
    """Aggregate invoice totals grouped by category over a recent window.

    **Why this exists**: an n8n workflow that builds a monthly Slack
    report should not have to fetch every invoice and re-aggregate
    client-side — that pattern hits memory limits in workflow runners
    and burns network round-trips. This endpoint pushes the work to
    Postgres (`GROUP BY` + `SUM`) and returns a constant-size payload.

    Defaults to the last 30 days in PLN. Invoices not yet categorised
    via `POST /invoices/{id}/categorize` show up under the `null`
    category bucket so the caller sees the un-categorised share.
    """
    if period_days < 1 or period_days > 365:
        raise HTTPException(
            status_code=400,
            detail="'period_days' must be between 1 and 365.",
        )
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(
            status_code=400,
            detail="'currency' must be a 3-letter ISO code (e.g. PLN, EUR).",
        )

    repo = InvoiceRepository(session)
    try:
        rows, total_count, grand_total = await repo.aggregate_by_category(
            period_days=period_days,
            currency=currency.upper(),
        )
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable.") from exc

    return InvoiceStats(
        period_days=period_days,
        currency=currency.upper(),
        total_invoices=total_count,
        grand_total_gross=grand_total,
        by_category=[
            CategoryStats(category=category, count=count, total_gross=total)
            for category, count, total in rows
        ],
    )


@app.get(
    "/invoices/search",
    response_model=SearchResponse,
    tags=["Invoices"],
)
async def search_invoices(
    q: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    store: Annotated[VectorStore, Depends(vector_store_dependency)],
    limit: int = 10,
) -> SearchResponse:
    """Semantic vector search over persisted invoices.

    Embeds the query with a multilingual MiniLM model and returns the
    closest invoices by cosine similarity. Works across language and
    paraphrase — try `Acme`, `konsulting`, or `software development`
    after uploading a few invoices. Each hit ships with the full
    invoice payload so you don't need a follow-up GET.
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query 'q' must not be empty.")
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="'limit' must be between 1 and 100.")

    query_vector = await asyncio.to_thread(embedder.embed, q)
    hits = await asyncio.to_thread(store.search, query_vector, limit)

    if not hits:
        return SearchResponse(query=q, results=[])

    repo = InvoiceRepository(session)
    results: list[SearchHit] = []
    try:
        for invoice_id, score in hits:
            row = await repo.get_by_id(invoice_id)
            if row is None:
                # Qdrant can out-live the DB record in edge cases (manual
                # row delete, restore from backup). Skip silently rather
                # than 500 — the user's result list just gets shorter.
                continue
            results.append(SearchHit(score=score, invoice=orm_to_stored_invoice(row)))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable.") from exc

    return SearchResponse(query=q, results=results)


@app.post(
    "/invoices/{invoice_id}/categorize",
    response_model=CategorizationResult,
    tags=["Invoices"],
)
async def categorize_invoice_endpoint(
    invoice_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    store: Annotated[VectorStore, Depends(vector_store_dependency)],
    response: Response,
    force: bool = False,
) -> CategorizationResult:
    """Classify an invoice into one of 12 expense categories using an LLM.

    **How it works**: retrieves similar invoices from the vector store
    (RAG), asks GPT-4o-mini to assign a category + confidence + Polish
    rationale, and caches the result on the invoice row.

    **Idempotent**: first call runs the LLM (`201 Created`, ~$0.0002);
    repeat calls return the cached classification instantly (`200 OK`).
    Pass `?force=true` to trigger a fresh LLM call.
    """
    try:
        result, was_fresh = await categorize_invoice(
            invoice_id, session=session, store=store, force=force
        )
    except InvoiceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvoiceCategorizationError as exc:
        raise HTTPException(status_code=502, detail=f"Categorization failed: {exc}") from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable.") from exc

    if was_fresh:
        response.status_code = 201
    return result


@app.get(
    "/invoices/{invoice_id}",
    response_model=StoredInvoice,
    tags=["Invoices"],
)
async def get_invoice(
    invoice_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> StoredInvoice:
    """Fetch a single invoice by its assigned id.

    The id is returned by `POST /invoices/ksef` (`id` field) and listed
    in `GET /invoices`. Try `1` if you've just uploaded the sample XML.
    """
    repo = InvoiceRepository(session)
    try:
        row = await repo.get_by_id(invoice_id)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database temporarily unavailable.") from exc
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
