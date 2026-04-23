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
