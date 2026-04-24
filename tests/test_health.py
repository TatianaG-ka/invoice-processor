"""Health-check endpoint tests."""

from fastapi.testclient import TestClient


def test_root_endpoint_returns_ok(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "invoice-processor"


def test_health_endpoint_returns_healthy(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_nonexistent_endpoint_returns_404(client: TestClient):
    response = client.get("/this-does-not-exist")
    assert response.status_code == 404
