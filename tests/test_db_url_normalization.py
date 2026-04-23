"""Unit tests for :func:`app.db.base._prepare_async_url`.

Users paste whatever connection string their managed Postgres provider
hands them. The normalisation layer adjusts the shape without forcing
hand-edits on the ``.env``, so the common ways a URL can arrive each
need a regression guard.
"""

from __future__ import annotations

import pytest

from app.db.base import _prepare_async_url


class TestPrefixRewrite:
    def test_bare_postgresql_prefix_gets_asyncpg(self):
        url, args = _prepare_async_url("postgresql://u:p@host/db")
        assert url == "postgresql+asyncpg://u:p@host/db"
        assert args == {}

    def test_existing_asyncpg_prefix_is_preserved(self):
        url, args = _prepare_async_url("postgresql+asyncpg://u:p@host/db")
        assert url == "postgresql+asyncpg://u:p@host/db"
        assert args == {}

    def test_sqlite_url_is_untouched(self):
        """Test DB URLs must pass through unchanged."""
        url, args = _prepare_async_url("sqlite+aiosqlite:///:memory:")
        assert url == "sqlite+aiosqlite:///:memory:"
        assert args == {}


class TestSslmodeStripping:
    def test_trailing_sslmode_is_stripped(self):
        url, args = _prepare_async_url("postgresql://u:p@host.neon.tech/db?sslmode=require")
        assert url == "postgresql+asyncpg://u:p@host.neon.tech/db"
        assert args == {"ssl": "require"}

    def test_sslmode_before_other_params_is_stripped(self):
        url, args = _prepare_async_url("postgresql://u:p@host/db?sslmode=require&options=other")
        assert url == "postgresql+asyncpg://u:p@host/db?options=other"
        assert args == {"ssl": "require"}

    def test_sslmode_after_other_params_is_stripped(self):
        url, args = _prepare_async_url("postgresql://u:p@host/db?options=other&sslmode=require")
        assert url == "postgresql+asyncpg://u:p@host/db?options=other"
        assert args == {"ssl": "require"}

    def test_no_sslmode_means_no_connect_args(self):
        url, args = _prepare_async_url("postgresql://u:p@host/db?options=other")
        assert url == "postgresql+asyncpg://u:p@host/db?options=other"
        assert args == {}


class TestNeonShape:
    def test_full_neon_shape_is_fully_normalised(self):
        """End-to-end: the exact shape Neon's dashboard produces."""
        original = (
            "postgresql://neondb_owner:xyz123@"
            "ep-example-12345.eu-central-1.aws.neon.tech/neondb?sslmode=require"
        )
        url, args = _prepare_async_url(original)
        assert url == (
            "postgresql+asyncpg://neondb_owner:xyz123@"
            "ep-example-12345.eu-central-1.aws.neon.tech/neondb"
        )
        assert args == {"ssl": "require"}


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@host/db",
        "postgresql+asyncpg://u:p@host/db",
        "sqlite+aiosqlite:///:memory:",
    ],
)
def test_normalisation_is_idempotent(url: str):
    """Re-running normalise on its own output must be a no-op."""
    once_url, once_args = _prepare_async_url(url)
    twice_url, twice_args = _prepare_async_url(once_url)
    assert once_url == twice_url
    assert once_args == twice_args
