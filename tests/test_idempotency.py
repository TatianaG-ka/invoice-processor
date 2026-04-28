"""Unit tests for :mod:`app.services.idempotency`.

The autouse ``_override_idempotency`` fixture in conftest swaps the
module-level singleton for ``fakeredis.aioredis.FakeRedis``, so these
tests exercise the real call paths against an in-memory Redis double —
no monkeypatching of the service itself is needed for the happy paths.
"""

from __future__ import annotations

import pytest
from redis.exceptions import RedisError

from app.services import idempotency


def test_ksef_key_is_namespaced_and_includes_seller_and_number():
    """The key must be unique per ``(seller_nip, invoice_number)``
    and live under the ``invoice-processor:idempotency:ksef:`` prefix
    so a shared Redis (Upstash) cannot collide with another tenant.
    """
    key = idempotency.ksef_key("0000000000", "FV/FA3/042/2026")
    assert key == "invoice-processor:idempotency:ksef:0000000000:FV/FA3/042/2026"


async def test_find_existing_returns_none_for_unknown_key():
    """First-time check: nothing stored → no cached duplicate."""
    key = idempotency.ksef_key("1111111111", "FV/UNKNOWN/001")
    assert await idempotency.find_existing(key) is None


async def test_claim_then_find_returns_invoice_id():
    """After ``claim``, ``find_existing`` returns the stored id."""
    key = idempotency.ksef_key("2222222222", "FV/CLAIM/001")
    await idempotency.claim(key, invoice_id=42, ttl_seconds=60)
    assert await idempotency.find_existing(key) == 42


async def test_claim_overwrites_existing_value():
    """Re-claiming the same key with a new id overwrites — needed by the
    stale-claim recovery path in ``POST /invoices/ksef``.
    """
    key = idempotency.ksef_key("3333333333", "FV/OVERWRITE/001")
    await idempotency.claim(key, invoice_id=1)
    await idempotency.claim(key, invoice_id=99)
    assert await idempotency.find_existing(key) == 99


async def test_find_existing_falls_through_on_redis_outage(monkeypatch):
    """Redis raising must degrade to "no dedup", never propagate the
    error to the caller — that is the contract the route relies on.
    """

    class _BoomClient:
        async def get(self, key):  # noqa: ARG002 — match real signature
            raise RedisError("connection refused")

    monkeypatch.setattr(idempotency, "_client", _BoomClient())
    assert await idempotency.find_existing("any-key") is None


async def test_claim_swallows_redis_outage(monkeypatch):
    """``claim`` is best-effort — a Redis outage after the row is saved
    must not raise, otherwise the caller would 500 *after* persisting,
    leaving a duplicate-prone but otherwise-fine record orphaned.
    """

    class _BoomClient:
        async def set(self, key, value, ex=None):  # noqa: ARG002
            raise RedisError("connection refused")

    monkeypatch.setattr(idempotency, "_client", _BoomClient())
    # Must not raise.
    await idempotency.claim("any-key", invoice_id=7)


async def test_find_existing_ignores_non_integer_payload(fake_async_redis):
    """A corrupted key (non-int payload) is treated as "no dedup" rather
    than crashing the route. Belt-and-braces: we always write ``str(int)``
    ourselves, so this path requires manual Redis tampering to hit.
    """
    await fake_async_redis.set("invoice-processor:idempotency:ksef:bad", "not-a-number")
    assert await idempotency.find_existing("invoice-processor:idempotency:ksef:bad") is None
