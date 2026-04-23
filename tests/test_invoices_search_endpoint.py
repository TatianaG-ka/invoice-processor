"""End-to-end tests for ``GET /invoices/search``.

Walk the full stack: upload a KSeF XML invoice (sync save + index →
fastest ingestion path), then query the search endpoint and assert
the saved invoice comes back as the top hit.

The conftest autouse fixtures wire in:

* an in-memory ``QdrantClient(":memory:")`` — no network / no server;
* a deterministic fake embedder — no model download, same text
  consistently produces the same vector, so upsert-then-search of the
  same text always returns that point first.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.db.base import get_sessionmaker
from app.db.models import Invoice

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _upload_ksef(client: TestClient, xml_bytes: bytes, filename: str = "f.xml") -> int:
    """POST a KSeF XML and return the resulting invoice id."""
    response = client.post(
        "/invoices/ksef",
        files={"file": (filename, xml_bytes, "application/xml")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ---------------------------------------------------------------------------
# Happy paths.
# ---------------------------------------------------------------------------


def test_search_returns_recently_saved_invoice(client: TestClient, ksef_fa2_bytes: bytes):
    """Upload → search for its seller name → top hit is that invoice."""
    invoice_id = _upload_ksef(client, ksef_fa2_bytes)

    response = client.get("/invoices/search", params={"q": "Acme Sp. z o.o."})
    assert response.status_code == 200
    body = response.json()

    assert body["query"] == "Acme Sp. z o.o."
    assert len(body["results"]) >= 1
    top = body["results"][0]
    assert top["invoice"]["id"] == invoice_id
    assert top["invoice"]["seller"]["name"] == "Acme Sp. z o.o."
    # Score is present and a finite float; the fake embedder can't
    # make assertions about absolute cosine values meaningful, so we
    # only check the shape. Real-vector similarity is asserted in
    # :mod:`tests.test_vector_store`.
    assert isinstance(top["score"], float)


def test_search_result_includes_full_invoice(client: TestClient, ksef_fa2_bytes: bytes):
    """Payload must hydrate the whole StoredInvoice, not just metadata."""
    _upload_ksef(client, ksef_fa2_bytes)

    response = client.get("/invoices/search", params={"q": "Acme Sp. z o.o."})
    top_invoice = response.json()["results"][0]["invoice"]

    # All the fields StoredInvoice promises must be present.
    assert "created_at" in top_invoice
    assert top_invoice["invoice_number"] == "FV/FA2/001/2026"
    assert top_invoice["totals"]["gross"] == "1230.00"
    assert len(top_invoice["line_items"]) == 2


def test_search_with_no_invoices_returns_empty_results(client: TestClient):
    """Empty collection → empty results, not 404."""
    response = client.get("/invoices/search", params={"q": "anything"})
    assert response.status_code == 200
    assert response.json() == {"query": "anything", "results": []}


def test_search_respects_limit(client: TestClient, ksef_fa2_bytes: bytes, ksef_fa3_bytes: bytes):
    """limit=1 must return only one hit even when more points exist."""
    _upload_ksef(client, ksef_fa2_bytes, filename="fa2.xml")
    _upload_ksef(client, ksef_fa3_bytes, filename="fa3.xml")

    response = client.get("/invoices/search", params={"q": "Acme Sp. z o.o.", "limit": 1})
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1


# ---------------------------------------------------------------------------
# Validation + edge cases.
# ---------------------------------------------------------------------------


def test_search_with_empty_query_returns_400(client: TestClient):
    response = client.get("/invoices/search", params={"q": "   "})
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_search_without_query_param_returns_422(client: TestClient):
    """Missing ``q`` is a FastAPI validation error (422)."""
    response = client.get("/invoices/search")
    assert response.status_code == 422


@pytest.mark.parametrize("limit", [0, -1, 101, 500])
def test_search_with_out_of_range_limit_returns_400(client: TestClient, limit: int):
    response = client.get("/invoices/search", params={"q": "anything", "limit": limit})
    assert response.status_code == 400


def test_search_route_takes_precedence_over_invoice_id(client: TestClient, ksef_fa2_bytes: bytes):
    """``/invoices/search`` must NOT be parsed as ``/invoices/{invoice_id}``.

    Regression guard: if the route order flips, the search endpoint
    disappears behind a 422 (``"search"`` can't parse as int).
    """
    _upload_ksef(client, ksef_fa2_bytes)
    response = client.get("/invoices/search", params={"q": "Acme"})
    assert response.status_code == 200


def test_search_skips_hits_whose_db_row_was_deleted(
    client: TestClient, ksef_fa2_bytes: bytes, test_vector_store
):
    """A stale Qdrant point (no DB row) must silently drop from results."""
    invoice_id = _upload_ksef(client, ksef_fa2_bytes)

    # Manually delete the DB row while leaving the Qdrant point intact
    # to simulate restore-from-backup / manual admin row delete.
    async def _delete() -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            row = await session.get(Invoice, invoice_id)
            assert row is not None
            await session.delete(row)
            await session.commit()

    asyncio.run(_delete())

    response = client.get("/invoices/search", params={"q": "Acme Sp. z o.o."})
    assert response.status_code == 200
    assert response.json()["results"] == []
