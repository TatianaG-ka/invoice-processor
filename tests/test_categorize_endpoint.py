"""End-to-end tests for ``POST /invoices/{invoice_id}/categorize``.

Patches :func:`app.services.invoice_categorizer._call_openai` to a
deterministic stub so the tests never hit OpenAI. The Qdrant store is
already swapped to in-memory by the autouse ``_override_vector_store``
fixture in :mod:`tests.conftest`, so retrieval is hermetic too.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.schemas.category import InvoiceCategory, LLMCategorizationResponse
from app.services import invoice_categorizer
from app.services.invoice_categorizer import (
    InvoiceCategorizationError,
)

# ---------------------------------------------------------------------------
# Helpers — seed an invoice via POST /invoices/ksef so each test starts
# with a real persisted row to categorize.
# ---------------------------------------------------------------------------


def _seed_invoice(client: TestClient, ksef_fa3_bytes: bytes) -> int:
    """POST a KSeF FA(3) sample and return the new invoice id."""
    response = client.post(
        "/invoices/ksef",
        files={"file": ("faktura.xml", ksef_fa3_bytes, "application/xml")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def stub_openai(monkeypatch):
    """Replace ``_call_openai`` with a deterministic IT-category stub."""

    def _stub(target, examples):
        return LLMCategorizationResponse(
            category=InvoiceCategory.IT,
            confidence=0.87,
            reasoning="Pozycje opisują usługi IT (hosting i wsparcie techniczne).",
        )

    monkeypatch.setattr(invoice_categorizer, "_call_openai", _stub)
    return _stub


# ---------------------------------------------------------------------------
# Happy path — first POST runs the LLM, returns 201.
# ---------------------------------------------------------------------------


def test_categorize_happy_path_returns_201_with_fresh_call(
    client: TestClient, ksef_fa3_bytes: bytes, stub_openai
):
    invoice_id = _seed_invoice(client, ksef_fa3_bytes)

    response = client.post(f"/invoices/{invoice_id}/categorize")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["invoice_id"] == invoice_id
    assert body["category"] == InvoiceCategory.IT.value
    assert body["confidence"] == pytest.approx(0.87)
    assert body["cached"] is False
    assert body["reasoning"]


# ---------------------------------------------------------------------------
# Idempotency — second POST returns 200 cached without calling the LLM.
# ---------------------------------------------------------------------------


def test_categorize_second_call_returns_200_cached(
    client: TestClient, ksef_fa3_bytes: bytes, monkeypatch
):
    """Once a row has a category, a second POST short-circuits to 200.

    Counts how many times ``_call_openai`` is invoked — exactly once
    across the two POSTs. The second call must read from Postgres.
    """
    invoice_id = _seed_invoice(client, ksef_fa3_bytes)
    call_count = {"n": 0}

    def _counting_stub(target, examples):
        call_count["n"] += 1
        return LLMCategorizationResponse(
            category=InvoiceCategory.CONSULTING,
            confidence=0.72,
            reasoning="Wzorzec usług doradczych.",
        )

    monkeypatch.setattr(invoice_categorizer, "_call_openai", _counting_stub)

    first = client.post(f"/invoices/{invoice_id}/categorize")
    assert first.status_code == 201
    assert first.json()["cached"] is False

    second = client.post(f"/invoices/{invoice_id}/categorize")
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["cached"] is True
    assert body["category"] == InvoiceCategory.CONSULTING.value
    assert body["confidence"] == pytest.approx(0.72)
    # No second LLM call — the cached path must not hit OpenAI.
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# Force re-categorization — ?force=true must call the LLM again and override.
# ---------------------------------------------------------------------------


def test_categorize_force_true_overrides_cache(
    client: TestClient, ksef_fa3_bytes: bytes, monkeypatch
):
    """``?force=true`` must call the LLM again and overwrite the column."""
    invoice_id = _seed_invoice(client, ksef_fa3_bytes)
    responses = [
        LLMCategorizationResponse(
            category=InvoiceCategory.IT,
            confidence=0.85,
            reasoning="Initial categorization.",
        ),
        LLMCategorizationResponse(
            category=InvoiceCategory.CONSULTING,
            confidence=0.66,
            reasoning="Reroll picked Consulting.",
        ),
    ]
    call_count = {"n": 0}

    def _alternating_stub(target, examples):
        result = responses[call_count["n"]]
        call_count["n"] += 1
        return result

    monkeypatch.setattr(invoice_categorizer, "_call_openai", _alternating_stub)

    first = client.post(f"/invoices/{invoice_id}/categorize")
    assert first.status_code == 201
    assert first.json()["category"] == InvoiceCategory.IT.value

    forced = client.post(f"/invoices/{invoice_id}/categorize?force=true")
    assert forced.status_code == 201, forced.text
    body = forced.json()
    assert body["cached"] is False
    assert body["category"] == InvoiceCategory.CONSULTING.value
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# 404 — invoice does not exist.
# ---------------------------------------------------------------------------


def test_categorize_404_when_invoice_missing(client: TestClient, stub_openai):
    response = client.post("/invoices/999999/categorize")
    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 502 — LLM raises a non-transient error after the retry policy gives up.
# ---------------------------------------------------------------------------


def test_categorize_502_when_llm_fails(client: TestClient, ksef_fa3_bytes: bytes, monkeypatch):
    invoice_id = _seed_invoice(client, ksef_fa3_bytes)

    def _raising_stub(target, examples):
        raise InvoiceCategorizationError("OpenAI returned malformed JSON")

    monkeypatch.setattr(invoice_categorizer, "_call_openai", _raising_stub)

    response = client.post(f"/invoices/{invoice_id}/categorize")
    assert response.status_code == 502
    assert "Categorization failed" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Persistence — after a fresh call, GET /invoices/{id} sees the category
# data in the row (proves the column was written, not just returned).
# ---------------------------------------------------------------------------


def test_categorize_persists_to_db_visible_via_repository(
    client: TestClient, ksef_fa3_bytes: bytes, stub_openai
):
    """After categorize, the underlying row carries the new column values.

    Re-categorizes implicitly via the cached path: a second POST with
    no force returns the same payload, which can only be true if the
    DB column was populated by the first call.
    """
    invoice_id = _seed_invoice(client, ksef_fa3_bytes)

    first = client.post(f"/invoices/{invoice_id}/categorize")
    assert first.status_code == 201

    cached = client.post(f"/invoices/{invoice_id}/categorize")
    assert cached.status_code == 200
    assert cached.json()["category"] == InvoiceCategory.IT.value
    assert cached.json()["confidence"] == pytest.approx(0.87)
    assert cached.json()["cached"] is True


# ---------------------------------------------------------------------------
# Zero-shot fallback — when Qdrant has no neighbours yet, the call still
# succeeds (the few-shot examples list is just empty).
# ---------------------------------------------------------------------------


def test_categorize_zero_shot_when_no_neighbours_indexed(
    client: TestClient, ksef_fa3_bytes: bytes, monkeypatch
):
    """Empty index → empty examples list → LLM still gets called zero-shot."""
    invoice_id = _seed_invoice(client, ksef_fa3_bytes)
    captured: dict = {}

    def _capturing_stub(target, examples):
        captured["examples_count"] = len(examples)
        return LLMCategorizationResponse(
            category=InvoiceCategory.OTHER,
            confidence=0.55,
            reasoning="Niewystarczające informacje, fallback do Inne.",
        )

    monkeypatch.setattr(invoice_categorizer, "_call_openai", _capturing_stub)

    response = client.post(f"/invoices/{invoice_id}/categorize")
    assert response.status_code == 201
    # The seeded invoice is the *only* invoice in the index, so it gets
    # filtered out (target == self) and zero examples remain.
    assert captured["examples_count"] == 0
    assert response.json()["category"] == InvoiceCategory.OTHER.value
