"""FastAPI dependency that yields an :class:`AsyncSession`.

Kept separate from :mod:`app.db.base` so tests can override the
dependency (via ``app.dependency_overrides[get_db] = ...``) without
reaching into engine/session-factory internals.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a scoped :class:`AsyncSession`, closed on request exit.

    The caller (a repository method or a route) is responsible for
    commit/rollback semantics. This dependency only handles acquire/
    release.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        yield session
