"""Tests for ``GET /invoices/jobs/{job_id}``.

The conftest queue runs in synchronous mode (``is_async=False``), so
a successful enqueue already leaves the job in the ``finished`` state
in fakeredis. A deliberately-broken PDF exercises the ``failed``
path; an arbitrary UUID exercises the 404 path.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import invoice_extractor


@pytest.fixture(autouse=True)
def force_mock_extractor(monkeypatch):
    """Keep the LLM out of the loop — task finishes via mock stub."""
    monkeypatch.setattr(invoice_extractor.settings, "EXTRACTOR_STRATEGY", "mock")


def test_job_status_unknown_id_returns_404(client: TestClient):
    response = client.get("/invoices/jobs/does-not-exist")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_job_status_finished_returns_invoice_id(client: TestClient, faktura_01_bytes: bytes):
    post = client.post(
        "/invoices",
        files={"file": ("faktura_01.pdf", faktura_01_bytes, "application/pdf")},
    )
    assert post.status_code == 202
    job_id = post.json()["job_id"]

    status = client.get(f"/invoices/jobs/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["job_id"] == job_id
    assert body["status"] == "finished"
    assert isinstance(body["invoice_id"], int) and body["invoice_id"] > 0
    assert body["error"] is None


def test_job_status_failed_returns_error_message(client: TestClient):
    post = client.post(
        "/invoices",
        files={"file": ("broken.pdf", b"not a pdf", "application/pdf")},
    )
    assert post.status_code == 202
    job_id = post.json()["job_id"]

    status = client.get(f"/invoices/jobs/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "failed"
    assert body["invoice_id"] is None
    # One-line exception summary, not a full traceback.
    assert body["error"]
    assert "\n" not in body["error"].strip()
