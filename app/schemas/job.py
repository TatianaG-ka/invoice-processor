"""Response schemas for the background-job endpoints.

These models describe the Phase 5 async-ingestion contract:

* :class:`JobAccepted` — what ``POST /invoices`` returns (202).
* :class:`JobStatus` — what ``GET /invoices/jobs/{job_id}`` returns.

Kept separate from :mod:`app.schemas.invoice` because the invoice
schemas describe the *domain payload* (extracted data) while these
describe *orchestration state* — different lifecycle, different owners.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# RQ job lifecycle: queued → started → (finished | failed | stopped).
# ``deferred`` and ``scheduled`` are additional states RQ can emit
# (dependencies, scheduled jobs) — we surface them verbatim rather
# than collapsing into a narrower union so the client sees what RQ
# actually reports.
JobStatusValue = Literal[
    "queued",
    "started",
    "deferred",
    "finished",
    "failed",
    "scheduled",
    "stopped",
    "canceled",
]


class JobAccepted(BaseModel):
    """Payload returned from ``POST /invoices`` (HTTP 202).

    The client polls :attr:`status_url` until :attr:`status` is
    ``finished`` (then fetches the invoice via
    ``GET /invoices/{invoice_id}``) or ``failed``.
    """

    job_id: str
    status: JobStatusValue
    status_url: str = Field(
        description="Absolute-path URL for polling job status.",
    )


class JobStatus(BaseModel):
    """Payload returned from ``GET /invoices/jobs/{job_id}``.

    :attr:`invoice_id` is populated only when :attr:`status` is
    ``finished`` and the job returned an integer primary key.
    :attr:`error` is populated only when :attr:`status` is ``failed``.
    """

    job_id: str
    status: JobStatusValue
    invoice_id: int | None = None
    error: str | None = Field(
        default=None,
        description="One-line exception summary when the job failed.",
    )
