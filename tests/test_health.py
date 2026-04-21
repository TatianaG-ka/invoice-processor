"""
Testy health checkowe - pierwsze testy w projekcie.

Pokazują pattern dla kolejnych testów:
- fixture 'client' jest dostępny z conftest.py
- testy API robisz przez TestClient
- asercje: status_code + zawartość JSON

Uruchom: pytest tests/test_health.py -v
"""

from fastapi.testclient import TestClient


def test_root_endpoint_returns_ok(client: TestClient):
    """GET / powinien zwrócić status ok."""
    response = client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "invoice-processor"


def test_health_endpoint_returns_healthy(client: TestClient):
    """GET /health powinien zwrócić healthy."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_nonexistent_endpoint_returns_404(client: TestClient):
    """Random endpoint powinien zwrócić 404."""
    response = client.get("/this-does-not-exist")
    assert response.status_code == 404


# =====================================================================
# TODO DZIEŃ 1 (wieczorem po dodaniu POST /invoices):
# =====================================================================
# Dodaj plik tests/test_invoices.py z testami:
#
# def test_post_invoice_with_pdf_returns_201(client, sample_pdf_bytes):
#     response = client.post(
#         "/invoices",
#         files={"file": ("test.pdf", sample_pdf_bytes, "application/pdf")}
#     )
#     assert response.status_code == 201
#
# def test_post_invoice_without_file_returns_422(client):
#     response = client.post("/invoices")
#     assert response.status_code == 422
#
# def test_post_invoice_with_wrong_type_returns_415(client):
#     response = client.post(
#         "/invoices",
#         files={"file": ("evil.exe", b"fake content", "application/x-executable")}
#     )
#     assert response.status_code == 415
# =====================================================================
