"""End-to-end persistence tests for the invoice endpoints.

These tests walk the full HTTP stack:

    POST /invoices (PDF upload, mock extractor) → DB row → GET /invoices/{id}

The ``force_mock_extractor`` fixture keeps the LLM out of the loop, so
the only moving pieces exercised here are the FastAPI routes, the
:class:`InvoiceRepository`, and the async SQLAlchemy layer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import invoice_extractor


@pytest.fixture(autouse=True)
def force_mock_extractor(monkeypatch):
    """Same guard as in ``test_invoices_endpoint.py`` — CI hermetic."""
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "mock")


def test_post_invoice_returns_id_and_created_at(client: TestClient, faktura_01_bytes: bytes):
    response = client.post(
        "/invoices",
        files={"file": ("faktura_01.pdf", faktura_01_bytes, "application/pdf")},
    )
    assert response.status_code == 201
    body = response.json()

    assert isinstance(body.get("id"), int)
    assert body["id"] > 0
    assert "created_at" in body
    assert body["created_at"]  # non-empty ISO timestamp


def test_post_then_get_roundtrip_returns_same_invoice(client: TestClient, faktura_01_bytes: bytes):
    post = client.post(
        "/invoices",
        files={"file": ("faktura_01.pdf", faktura_01_bytes, "application/pdf")},
    )
    assert post.status_code == 201
    invoice_id = post.json()["id"]

    got = client.get(f"/invoices/{invoice_id}")
    assert got.status_code == 200
    body = got.json()

    assert body["id"] == invoice_id
    assert body["seller"]["name"] == post.json()["seller"]["name"]
    assert body["totals"]["currency"] == post.json()["totals"]["currency"]


def test_get_invoice_missing_returns_404(client: TestClient):
    response = client.get("/invoices/999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_each_post_creates_a_new_row(client: TestClient, faktura_01_bytes: bytes):
    """Two uploads → two distinct DB IDs."""
    first = client.post(
        "/invoices",
        files={"file": ("a.pdf", faktura_01_bytes, "application/pdf")},
    )
    second = client.post(
        "/invoices",
        files={"file": ("b.pdf", faktura_01_bytes, "application/pdf")},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_post_invoice_body_preserves_extracted_schema(client: TestClient, faktura_01_bytes: bytes):
    """Adding ``id`` + ``created_at`` does not break the Phase 2 shape."""
    response = client.post(
        "/invoices",
        files={"file": ("faktura_01.pdf", faktura_01_bytes, "application/pdf")},
    )
    assert response.status_code == 201
    body = response.json()

    # Superset — id/created_at add to, not replace, the extracted shape.
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
