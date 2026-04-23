"""Regression tests for DB-error handling on the HTTP boundary.

Review finding **IMPORTANT-2**: endpoints that touch the repository
must surface ``SQLAlchemyError`` (cold Neon start, connection spike,
``OperationalError``) as ``503 Service Unavailable`` with a safe body
— not a raw 500 plus an SQLAlchemy stack trace. A recruiter hitting
the demo URL while the DB has a hiccup should not see Python internals.

Each test patches the relevant ``InvoiceRepository`` method to raise
``OperationalError`` (a concrete ``SQLAlchemyError`` subclass) and
asserts the response is a clean 503.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.repositories.invoice_repository import InvoiceRepository


def _boom(*_args, **_kwargs):
    raise OperationalError("SELECT 1", {}, Exception("connection refused"))


class TestKsefEndpoint503:
    def test_post_ksef_returns_503_when_db_down(
        self, client: TestClient, ksef_fa2_bytes: bytes, monkeypatch: pytest.MonkeyPatch
    ):
        async def _raise(self, extracted):
            _boom()

        monkeypatch.setattr(InvoiceRepository, "save", _raise)

        response = client.post(
            "/invoices/ksef",
            files={"file": ("f.xml", ksef_fa2_bytes, "application/xml")},
        )
        assert response.status_code == 503
        body = response.json()
        assert body == {"detail": "Database temporarily unavailable."}
        # Belt-and-braces: SQLAlchemy internals must not leak.
        assert "OperationalError" not in response.text
        assert "Traceback" not in response.text


class TestGetByIdEndpoint503:
    def test_get_invoice_returns_503_when_db_down(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        async def _raise(self, invoice_id):
            _boom()

        monkeypatch.setattr(InvoiceRepository, "get_by_id", _raise)

        response = client.get("/invoices/1")
        assert response.status_code == 503
        assert response.json() == {"detail": "Database temporarily unavailable."}

    def test_404_path_still_works_when_db_is_healthy(self, client: TestClient):
        """Sanity: the 503 branch doesn't swallow the ordinary 404 case."""
        response = client.get("/invoices/9999")
        assert response.status_code == 404


class TestSearchEndpoint503:
    def test_search_returns_503_when_hydration_fails(
        self,
        client: TestClient,
        ksef_fa2_bytes: bytes,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """A DB failure during result hydration must 503, not 500.

        We save a row successfully (so Qdrant has a hit to return) and
        only then break ``get_by_id`` — simulating a connection drop
        mid-response.
        """
        upload = client.post(
            "/invoices/ksef",
            files={"file": ("f.xml", ksef_fa2_bytes, "application/xml")},
        )
        assert upload.status_code == 201

        async def _raise(self, invoice_id):
            _boom()

        monkeypatch.setattr(InvoiceRepository, "get_by_id", _raise)

        response = client.get("/invoices/search", params={"q": "Acme Sp. z o.o."})
        assert response.status_code == 503
        assert response.json() == {"detail": "Database temporarily unavailable."}
