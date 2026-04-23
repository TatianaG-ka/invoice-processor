"""Database layer (async SQLAlchemy 2.0).

Public API:

* :class:`app.db.base.Base` — declarative base for all ORM models.
* :func:`app.db.base.get_engine` / :func:`app.db.base.get_sessionmaker`
  — lazy singletons driven by ``settings.DATABASE_URL``.
* :func:`app.db.session.get_db` — FastAPI dependency yielding
  :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
* :class:`app.db.models.Invoice` — persisted invoice row.
* :class:`app.db.repositories.invoice_repository.InvoiceRepository` —
  write/read helper on top of an ``AsyncSession``.

Phase 3 intentionally skips Alembic; schema is created via
``Base.metadata.create_all`` in the FastAPI lifespan startup hook. The
ADR lives in ``README`` (Phase 7).
"""
