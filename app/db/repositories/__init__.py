"""Repository layer — thin async wrappers around ORM access.

Repositories take an :class:`~sqlalchemy.ext.asyncio.AsyncSession` as
a constructor argument, not a module-level singleton, so testing can
use a transaction-scoped session without monkeypatching globals.
"""
