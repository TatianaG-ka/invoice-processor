"""Pytest fixtures - reużywalne obiekty dla testów.

Importowane automatycznie przez pytest w każdym pliku testowym.

Phase 3 addition: every test runs against an in-memory
``sqlite+aiosqlite`` database. The shared fixture creates/destroys the
schema per test and overrides the FastAPI ``get_db`` dependency so the
``client`` TestClient sees the test DB rather than the real Postgres
URL configured in ``settings``.

Phase 5 addition: every test also gets a fakeredis-backed RQ queue
running in synchronous mode (``is_async=False``). Enqueued jobs
execute inline inside the route handler so the endpoint tests observe
a realistic 202 → finished lifecycle without running a worker
process. The same autouse fixture retargets
:func:`app.db.base.get_sessionmaker` at the in-memory test engine so
the task function's ``asyncio.run`` pipeline hits the test DB too.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import fakeredis
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from rq import Queue
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db import base as db_base
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.queue import connection as queue_connection
from app.queue.connection import queue_dependency

# ---------------------------------------------------------------------------
# Async DB fixtures — in-memory SQLite per test.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_engine():
    """Create a fresh in-memory SQLite engine per test.

    ``StaticPool`` keeps the same in-memory database across connections
    opened within this engine — otherwise every new connection would
    see an empty DB, which breaks any flow that opens >1 connection
    (FastAPI + the repository).
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def test_session_factory(test_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(test_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """An :class:`AsyncSession` for repository-level tests."""
    async with test_session_factory() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def _override_get_db(test_session_factory, test_engine, monkeypatch):
    """Route every database access at the test engine.

    Two layers of override are needed:

    1. ``app.dependency_overrides[get_db]`` — the FastAPI-level hook
       used by async routes (``GET /invoices/{id}``, KSeF upload).
    2. ``app.db.base._sessionmaker`` / ``_engine`` — the module-level
       singletons used by the queue task function
       (:func:`app.queue.tasks.process_pdf_invoice`), which runs
       outside FastAPI's DI and calls
       :func:`app.db.base.get_sessionmaker` directly.

    Autouse so every test gets both overrides regardless of whether
    it touches the DB — defence in depth against accidental real-
    Postgres connections.
    """

    async def _get_test_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_test_db
    # Prime the lazy singletons so queue tasks share the test DB.
    monkeypatch.setattr(db_base, "_engine", test_engine)
    monkeypatch.setattr(db_base, "_sessionmaker", test_session_factory)
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Queue fixtures — fakeredis + synchronous RQ queue (Phase 5).
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis() -> fakeredis.FakeStrictRedis:
    """Per-test fakeredis connection.

    Each test gets its own isolated instance so job ids from one
    test never leak into another.
    """
    return fakeredis.FakeStrictRedis()


@pytest.fixture
def test_queue(fake_redis: fakeredis.FakeStrictRedis) -> Queue:
    """Synchronous RQ queue backed by fakeredis.

    ``is_async=False`` means ``queue.enqueue(...)`` executes the job
    inline and the returned :class:`~rq.job.Job` is already finished
    (or failed) by the time the enqueue call returns. No worker
    process needed.
    """
    return Queue("default", connection=fake_redis, is_async=False)


@pytest.fixture(autouse=True)
def _override_queue(test_queue: Queue, monkeypatch):
    """Wire the test queue into every FastAPI queue lookup.

    Overrides both:

    * the FastAPI dependency (``queue_dependency``) — used by
      ``POST /invoices`` and ``GET /invoices/jobs/{id}`` routes;
    * the module-level singletons in :mod:`app.queue.connection` —
      anything else that resolves the queue without going through
      the dependency (currently nothing, but cheap insurance).

    Autouse so no test can accidentally hit real Redis via
    ``settings.REDIS_URL``.
    """
    app.dependency_overrides[queue_dependency] = lambda: test_queue
    monkeypatch.setattr(queue_connection, "_redis_client", test_queue.connection)
    monkeypatch.setattr(queue_connection, "_queue", test_queue)
    try:
        yield
    finally:
        app.dependency_overrides.pop(queue_dependency, None)


# ---------------------------------------------------------------------------
# HTTP clients.
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """TestClient dla endpointów FastAPI (synchronous)."""
    return TestClient(app)


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """httpx.AsyncClient against the ASGI app (for async tests)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# PDF fixtures (Phase 1).
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# KSeF XML fixtures (Phase 4).
# ---------------------------------------------------------------------------


@pytest.fixture
def ksef_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "ksef"


@pytest.fixture
def ksef_fa2_bytes(ksef_dir: Path) -> bytes:
    return _load_fixture(ksef_dir, "faktura_fa2_sample.xml")


@pytest.fixture
def ksef_fa3_bytes(ksef_dir: Path) -> bytes:
    return _load_fixture(ksef_dir, "faktura_fa3_sample.xml")
