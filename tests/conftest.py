"""
Pytest fixtures - reużywalne obiekty dla testów.

Importowane automatycznie przez pytest w każdym pliku testowym.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    """TestClient dla endpointów FastAPI."""
    return TestClient(app)


@pytest.fixture
def fixtures_dir() -> Path:
    """Ścieżka do katalogu z testowymi plikami."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_pdf_bytes(fixtures_dir: Path) -> bytes:
    """
    Zawartość testowego PDF-a jako bajty.

    UWAGA: Musisz położyć prawdziwy PDF w tests/fixtures/sample_invoice.pdf
    Możesz użyć dowolnej faktury - wygeneruj przez fakture.pl albo utwórz sama.
    """
    pdf_path = fixtures_dir / "sample_invoice.pdf"
    if not pdf_path.exists():
        pytest.skip(f"Brak pliku {pdf_path}. Dodaj testowy PDF.")
    return pdf_path.read_bytes()


# =====================================================================
# TODO DZIEŃ 3: Dodaj fixture dla sesji DB
# =====================================================================
# @pytest.fixture
# def test_db():
#     """
#     Izolowana baza dla testów.
#     Użyj SQLite in-memory albo osobnej bazy testowej.
#     """
#     from app.database import Base, engine, SessionLocal
#     Base.metadata.create_all(bind=engine)
#     db = SessionLocal()
#     try:
#         yield db
#     finally:
#         db.close()
#         Base.metadata.drop_all(bind=engine)
# =====================================================================


# =====================================================================
# TODO DZIEŃ 4: Dodaj mock OpenAI
# =====================================================================
# @pytest.fixture
# def mock_openai(monkeypatch):
#     """Mock OpenAI API - nie wywołuj prawdziwego API w testach."""
#     def fake_completion(*args, **kwargs):
#         return {"choices": [{"message": {"content": '{"nip":"1234567890"}'}}]}
#     monkeypatch.setattr("openai.ChatCompletion.create", fake_completion)
# =====================================================================
