"""End-to-end tests for ``POST /invoices/ksef``.

Walk the full HTTP stack: XML upload → parser → repository → response.
The KSeF parser is deterministic (no network), so no mocking is
required for the happy path.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_post_ksef_fa2_returns_stored_invoice(client: TestClient, ksef_fa2_bytes: bytes):
    response = client.post(
        "/invoices/ksef",
        files={"file": ("faktura_fa2.xml", ksef_fa2_bytes, "application/xml")},
    )
    assert response.status_code == 201
    body = response.json()

    assert isinstance(body["id"], int)
    assert body["id"] > 0
    assert body["invoice_number"] == "FV/FA2/001/2026"
    assert body["seller"]["name"] == "Acme Sp. z o.o."
    assert body["totals"]["gross"] == "1230.00"
    assert len(body["line_items"]) == 2


def test_post_ksef_fa3_returns_stored_invoice(client: TestClient, ksef_fa3_bytes: bytes):
    response = client.post(
        "/invoices/ksef",
        files={"file": ("faktura_fa3.xml", ksef_fa3_bytes, "application/xml")},
    )
    assert response.status_code == 201
    body = response.json()

    assert body["invoice_number"] == "FV/FA3/042/2026"
    assert Decimal(body["totals"]["gross"]) == Decimal("6150.00")


def test_post_ksef_accepts_text_xml_mime(client: TestClient, ksef_fa3_bytes: bytes):
    """Legacy ``text/xml`` MIME type must also be accepted."""
    response = client.post(
        "/invoices/ksef",
        files={"file": ("faktura.xml", ksef_fa3_bytes, "text/xml")},
    )
    assert response.status_code == 201


def test_post_ksef_then_get_by_id_roundtrip(client: TestClient, ksef_fa3_bytes: bytes):
    """Invoices ingested via KSeF are readable by the shared GET /invoices/{id}."""
    post = client.post(
        "/invoices/ksef",
        files={"file": ("faktura.xml", ksef_fa3_bytes, "application/xml")},
    )
    assert post.status_code == 201
    invoice_id = post.json()["id"]

    got = client.get(f"/invoices/{invoice_id}")
    assert got.status_code == 200
    assert got.json()["invoice_number"] == "FV/FA3/042/2026"


# ---------------------------------------------------------------------------
# Idempotency — duplicate POST returns 200 with the same id, no new row.
# ---------------------------------------------------------------------------


def test_post_ksef_duplicate_returns_200_with_same_id(client: TestClient, ksef_fa3_bytes: bytes):
    """Two POSTs of the same KSeF XML → first 201 (created), second 200 (cached).

    The second response carries the *same* invoice_id as the first, so
    downstream consumers (n8n, Slack notifications) treat retries as
    no-ops instead of duplicating records.
    """
    first = client.post(
        "/invoices/ksef",
        files={"file": ("faktura.xml", ksef_fa3_bytes, "application/xml")},
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = client.post(
        "/invoices/ksef",
        files={"file": ("faktura.xml", ksef_fa3_bytes, "application/xml")},
    )
    assert second.status_code == 200
    assert second.json()["id"] == first_id
    # Body must be identical, not a degraded "already exists" stub —
    # the client should not have to special-case 200 vs 201.
    assert second.json()["invoice_number"] == first.json()["invoice_number"]
    assert second.json()["totals"]["gross"] == first.json()["totals"]["gross"]


def test_post_ksef_different_invoices_both_get_201(
    client: TestClient, ksef_fa2_bytes: bytes, ksef_fa3_bytes: bytes
):
    """Different ``(seller_nip, invoice_number)`` keys → both create rows.

    The dedup key is per-invoice, not per-request, so the FA(2) sample
    and FA(3) sample (different invoice numbers + sellers) must both
    land as 201 with distinct ids.
    """
    fa2 = client.post(
        "/invoices/ksef",
        files={"file": ("fa2.xml", ksef_fa2_bytes, "application/xml")},
    )
    fa3 = client.post(
        "/invoices/ksef",
        files={"file": ("fa3.xml", ksef_fa3_bytes, "application/xml")},
    )
    assert fa2.status_code == 201
    assert fa3.status_code == 201
    assert fa2.json()["id"] != fa3.json()["id"]


# ---------------------------------------------------------------------------
# Error paths.
# ---------------------------------------------------------------------------


def test_post_ksef_without_file_returns_422(client: TestClient):
    response = client.post("/invoices/ksef")
    assert response.status_code == 422


def test_post_ksef_wrong_content_type_returns_415(client: TestClient, ksef_fa3_bytes: bytes):
    response = client.post(
        "/invoices/ksef",
        files={"file": ("faktura.pdf", ksef_fa3_bytes, "application/pdf")},
    )
    assert response.status_code == 415


def test_post_ksef_malformed_xml_returns_422(client: TestClient):
    response = client.post(
        "/invoices/ksef",
        files={"file": ("broken.xml", b"<not-xml>unterminated", "application/xml")},
    )
    assert response.status_code == 422


def test_post_ksef_unsupported_namespace_returns_422(client: TestClient):
    xml = b'<?xml version="1.0" encoding="UTF-8"?>' b'<Faktura xmlns="http://example.com/unknown"/>'
    response = client.post(
        "/invoices/ksef",
        files={"file": ("bad.xml", xml, "application/xml")},
    )
    assert response.status_code == 422
    assert "Unsupported KSeF namespace" in response.json()["detail"]


def test_post_ksef_empty_body_returns_422(client: TestClient):
    response = client.post(
        "/invoices/ksef",
        files={"file": ("empty.xml", b"", "application/xml")},
    )
    assert response.status_code == 422
