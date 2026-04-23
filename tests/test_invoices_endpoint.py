"""End-to-end tests for ``POST /invoices``.

These tests exercise the full Phase 2 pipeline — validation →
``pdf_text_extractor`` → ``invoice_extractor`` → response — using the
real fixture PDFs from ``docs/dane_testowe/``.

OpenAI is never called. The ``force_mock_extractor`` fixture toggles
``EXTRACTOR_STRATEGY`` to ``"mock"`` for every test in this module, so
CI stays hermetic and the endpoint response is deterministic.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import invoice_extractor


@pytest.fixture(autouse=True)
def force_mock_extractor(monkeypatch):
    """Force the extractor into mock mode for every test in this file.

    Protects CI where ``OPENAI_API_KEY`` is absent, and protects local
    runs where the key *is* set — we never want the test suite to hit
    the network.
    """
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "mock")


# ---------------------------------------------------------------------------
# Happy path — each fixture returns a schema-shaped payload.
# ---------------------------------------------------------------------------


def test_post_invoice_with_pdf_returns_201(client: TestClient, faktura_01_bytes: bytes):
    response = client.post(
        "/invoices",
        files={"file": ("faktura_01.pdf", faktura_01_bytes, "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()

    # Schema-level assertions (works regardless of mock/real mode).
    assert set(body.keys()) >= {
        "invoice_number",
        "issue_date",
        "seller",
        "buyer",
        "line_items",
        "totals",
    }
    assert set(body["seller"].keys()) >= {"name", "nip", "address"}
    assert set(body["totals"].keys()) == {"net", "vat", "gross", "currency"}
    assert isinstance(body["line_items"], list)


def test_post_each_fixture_returns_201(client: TestClient, all_faktury_bytes: bytes):
    """Parametrized — hit the endpoint once per synthetic invoice."""
    response = client.post(
        "/invoices",
        files={"file": ("faktura.pdf", all_faktury_bytes, "application/pdf")},
    )
    assert response.status_code == 201
    body = response.json()
    assert "totals" in body
    assert body["totals"]["currency"] in {"PLN", "EUR", "USD"}


def test_post_invoice_returns_mock_payload_in_mock_mode(
    client: TestClient, faktura_01_bytes: bytes
):
    """Mock-mode response should be clearly identifiable.

    Protects against the mock payload leaking into production unseen.
    """
    response = client.post(
        "/invoices",
        files={"file": ("faktura_01.pdf", faktura_01_bytes, "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["seller"]["name"].startswith("MOCK")


# ---------------------------------------------------------------------------
# Validation errors.
# ---------------------------------------------------------------------------


def test_post_without_file_returns_422(client: TestClient):
    response = client.post("/invoices")
    assert response.status_code == 422


def test_post_wrong_content_type_returns_415(client: TestClient):
    response = client.post(
        "/invoices",
        files={
            "file": ("evil.exe", b"fake content", "application/x-executable"),
        },
    )
    assert response.status_code == 415
    assert "Unsupported" in response.json()["detail"]


def test_post_image_content_type_returns_415_with_phase5_hint(client: TestClient):
    """JPG/PNG are planned for Phase 5 (OCR path) — for now, 415."""
    response = client.post(
        "/invoices",
        files={"file": ("scan.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert response.status_code == 415
    assert "Phase 5" in response.json()["detail"]


def test_post_corrupted_pdf_returns_422(client: TestClient):
    """Non-PDF bytes sent with PDF content-type → extractor raises → 422."""
    response = client.post(
        "/invoices",
        files={"file": ("broken.pdf", b"this is not a pdf", "application/pdf")},
    )
    assert response.status_code == 422


def test_post_empty_file_returns_422(client: TestClient):
    """Zero-byte upload trips the extractor's empty-bytes guard."""
    response = client.post(
        "/invoices",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 422
