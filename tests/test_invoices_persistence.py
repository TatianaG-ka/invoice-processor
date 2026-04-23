"""End-to-end persistence tests for the invoice endpoints.

Post-Phase-5, the PDF upload path is async:

    POST /invoices (PDF)  →  202 {job_id}
    GET  /invoices/jobs/{job_id}  →  finished + invoice_id
    GET  /invoices/{invoice_id}   →  stored record

These tests walk the full HTTP stack and rely on the synchronous queue
fixture in ``conftest.py`` so the job finishes inline — there is no
worker process during tests.

The ``force_mock_extractor`` fixture keeps the LLM out of the loop, so
the only moving pieces exercised here are the FastAPI routes, the RQ
wiring, the :class:`InvoiceRepository`, and the async SQLAlchemy layer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import invoice_extractor


@pytest.fixture(autouse=True)
def force_mock_extractor(monkeypatch):
    """Same guard as in ``test_invoices_endpoint.py`` — CI hermetic."""
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "mock")


def _post_and_wait_for_invoice_id(client: TestClient, pdf_bytes: bytes) -> int:
    """POST a PDF, fetch the job, return the invoice_id.

    Helper for the round-trip tests. Sync queue mode means the job
    finishes inside ``enqueue``, so the status check is deterministic
    without polling.
    """
    post = client.post(
        "/invoices",
        files={"file": ("faktura.pdf", pdf_bytes, "application/pdf")},
    )
    assert post.status_code == 202
    status = client.get(f"/invoices/jobs/{post.json()['job_id']}").json()
    assert status["status"] == "finished", status
    assert isinstance(status["invoice_id"], int)
    return status["invoice_id"]


def test_post_then_get_roundtrip_returns_stored_invoice(
    client: TestClient, faktura_01_bytes: bytes
):
    invoice_id = _post_and_wait_for_invoice_id(client, faktura_01_bytes)

    got = client.get(f"/invoices/{invoice_id}")
    assert got.status_code == 200
    body = got.json()

    assert body["id"] == invoice_id
    assert "created_at" in body and body["created_at"]
    assert body["totals"]["currency"] in {"PLN", "EUR", "USD"}


def test_get_invoice_missing_returns_404(client: TestClient):
    response = client.get("/invoices/999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_each_post_creates_a_new_row(client: TestClient, faktura_01_bytes: bytes):
    """Two uploads → two distinct DB IDs."""
    first = _post_and_wait_for_invoice_id(client, faktura_01_bytes)
    second = _post_and_wait_for_invoice_id(client, faktura_01_bytes)
    assert first != second


def test_stored_invoice_preserves_extracted_schema(client: TestClient, faktura_01_bytes: bytes):
    """The persisted record still carries the Phase 2 shape.

    ``id`` + ``created_at`` add to the extracted shape without
    displacing any Phase 2 field.
    """
    invoice_id = _post_and_wait_for_invoice_id(client, faktura_01_bytes)
    body = client.get(f"/invoices/{invoice_id}").json()

    assert set(body.keys()) >= {
        "id",
        "created_at",
        "invoice_number",
        "issue_date",
        "seller",
        "buyer",
        "line_items",
        "totals",
    }
