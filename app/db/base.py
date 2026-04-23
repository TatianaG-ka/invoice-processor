"""Async SQLAlchemy engine + declarative base.

Single place that knows how to talk to the database. The rest of the
application works against the :class:`Base` metaclass and the session
factory returned by :func:`get_sessionmaker` — never against the raw
engine.

The engine is built **lazily** on first use. That matters for two
reasons:

1. Tests can call :func:`reset_engine` after overriding
   ``settings.DATABASE_URL`` and get a fresh engine pointed at an
   in-memory SQLite database.
2. Importing :mod:`app.db.base` during module discovery (e.g. when
   Alembic-free ``create_all`` runs at app startup) does not open a
   connection to a database that may not exist yet.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Shared declarative base for every ORM model in :mod:`app.db.models`."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the lazily-constructed async engine.

    ``future=True`` is implicit in SQLAlchemy 2.x. ``expire_on_commit=False``
    is applied on the session factory, not the engine.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the lazily-constructed async session factory."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _sessionmaker


async def reset_engine() -> None:
    """Dispose the engine and drop the cached session factory.

    Used by tests that swap ``settings.DATABASE_URL`` between module
    imports and need the next :func:`get_engine` call to honour the new
    URL.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def create_all() -> None:
    """Create every table declared on :class:`Base`.

    Invoked from the FastAPI lifespan startup hook — the portfolio
    project skips Alembic by design (ADR in README).
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
