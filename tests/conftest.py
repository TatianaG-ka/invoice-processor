"""Pytest fixtures - reużywalne obiekty dla testów.

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
    """Katalog z syntetycznymi fakturami testowymi.

    PDFy żyją w `<repo>/docs/dane_testowe/`, nie w `<repo>/tests/fixtures/`,
    bo są również częścią dokumentacji projektu (synthetic samples opisane
    w README). Jeden kanoniczny path zamiast duplikatów.
    """
    return Path(__file__).resolve().parent.parent / "docs" / "dane_testowe"


def _load_fixture(fixtures_dir: Path, name: str) -> bytes:
    path = fixtures_dir / name
    if not path.exists():
        pytest.skip(f"Brak pliku {path}. Uruchom `scripts/generate_test_pdf.py`.")
    return path.read_bytes()


@pytest.fixture
def faktura_01_bytes(fixtures_dir: Path) -> bytes:
    """Prosty układ faktury."""
    return _load_fixture(fixtures_dir, "faktura_01_prosta.pdf")


@pytest.fixture
def faktura_02_bytes(fixtures_dir: Path) -> bytes:
    """Faktura z tabelą pozycji."""
    return _load_fixture(fixtures_dir, "faktura_02_z_tabela.pdf")


@pytest.fixture
def faktura_03_bytes(fixtures_dir: Path) -> bytes:
    """Wariant angielski."""
    return _load_fixture(fixtures_dir, "faktura_03_english.pdf")


@pytest.fixture
def faktura_04_bytes(fixtures_dir: Path) -> bytes:
    """Duże kwoty."""
    return _load_fixture(fixtures_dir, "faktura_04_duze_kwoty.pdf")


@pytest.fixture
def faktura_05_bytes(fixtures_dir: Path) -> bytes:
    """Minimalne wymagane pola."""
    return _load_fixture(fixtures_dir, "faktura_05_minimalna.pdf")


@pytest.fixture(
    params=[
        "faktura_01_prosta.pdf",
        "faktura_02_z_tabela.pdf",
        "faktura_03_english.pdf",
        "faktura_04_duze_kwoty.pdf",
        "faktura_05_minimalna.pdf",
    ],
    ids=["prosta", "z_tabela", "english", "duze_kwoty", "minimalna"],
)
def all_faktury_bytes(request: pytest.FixtureRequest, fixtures_dir: Path) -> bytes:
    """Parametrized fixture — każdy test używający tego fixture'u przechodzi
    5× (raz per synthetic invoice)."""
    return _load_fixture(fixtures_dir, request.param)


@pytest.fixture
def sample_pdf_bytes(faktura_01_bytes: bytes) -> bytes:
    """Backward-compat alias dla pierwszej faktury."""
    return faktura_01_bytes
