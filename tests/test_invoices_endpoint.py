"""End-to-end tests for ``POST /invoices``.

Phase 5 refactor: the endpoint is now fire-and-forget — it validates
the upload, enqueues a background job, and returns 202 with a job id.
The conftest queue fixture runs RQ in synchronous mode
(``is_async=False``), so by the time ``enqueue`` returns the job is
already ``finished`` in fakeredis. That gives us a realistic
202 → status=finished → stored invoice flow inside a single test
without running a worker process.

OpenAI is never called. The ``force_mock_extractor`` fixture toggles
``EXTRACTOR_STRATEGY`` to ``"mock"`` for every test in this module, so
CI stays hermetic and the pipeline output is deterministic.
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
# Happy path — 202 + enqueued job that finishes synchronously.
# ---------------------------------------------------------------------------


def test_post_invoice_returns_202_with_job_accepted(client: TestClient, faktura_01_bytes: bytes):
    response = client.post(
        "/invoices",
        files={"file": ("faktura_01.pdf", faktura_01_bytes, "application/pdf")},
    )

    assert response.status_code == 202
    body = response.json()
    assert set(body.keys()) == {"job_id", "status", "status_url"}
    assert body["job_id"]
    assert body["status"] in {"queued", "finished"}  # sync queue may already be finished
    assert body["status_url"] == f"/invoices/jobs/{body['job_id']}"


def test_post_each_fixture_enqueues_and_finishes(client: TestClient, all_faktury_bytes: bytes):
    """Parametrized — one invoice per synthetic fixture lands in the DB.

    Sync queue mode means the job runs inline; after POST we can
    immediately fetch its status and it should already be finished.
    """
    post = client.post(
        "/invoices",
        files={"file": ("faktura.pdf", all_faktury_bytes, "application/pdf")},
    )
    assert post.status_code == 202
    job_id = post.json()["job_id"]

    status = client.get(f"/invoices/jobs/{job_id}")
    assert status.status_code == 200
    status_body = status.json()
    assert status_body["status"] == "finished"
    assert isinstance(status_body["invoice_id"], int)
    assert status_body["invoice_id"] > 0


def test_post_then_fetch_invoice_returns_mock_signal(client: TestClient, faktura_01_bytes: bytes):
    """Mock-mode response should be clearly identifiable in the stored row.

    Protects against the mock payload leaking into production unseen.
    """
    post = client.post(
        "/invoices",
        files={"file": ("faktura_01.pdf", faktura_01_bytes, "application/pdf")},
    )
    job_id = post.json()["job_id"]
    status = client.get(f"/invoices/jobs/{job_id}").json()
    invoice = client.get(f"/invoices/{status['invoice_id']}").json()
    assert invoice["seller"]["name"].startswith("MOCK")


# ---------------------------------------------------------------------------
# Validation errors — enforced before the queue is touched.
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


def test_post_image_content_type_returns_415(client: TestClient):
    """JPG/PNG remain 415 — client must rasterise into a single-page PDF."""
    response = client.post(
        "/invoices",
        files={"file": ("scan.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert response.status_code == 415


def test_post_empty_file_returns_422(client: TestClient):
    """Zero-byte upload rejected synchronously (no job wasted)."""
    response = client.post(
        "/invoices",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 422


def test_post_corrupted_pdf_enqueues_but_job_fails(client: TestClient):
    """Non-PDF bytes still pass content-type validation → job fails.

    The route accepts the upload (it passes the cheap checks) and
    enqueues the work; the worker is the one that discovers the
    payload is garbage, so the failure surfaces via the job status
    endpoint rather than as a direct 422 on POST.
    """
    post = client.post(
        "/invoices",
        files={"file": ("broken.pdf", b"this is not a pdf", "application/pdf")},
    )
    assert post.status_code == 202

    status = client.get(f"/invoices/jobs/{post.json()['job_id']}").json()
    assert status["status"] == "failed"
    assert status["invoice_id"] is None
    assert status["error"]
    assert "PDF" in status["error"] or "pdf" in status["error"].lower()
