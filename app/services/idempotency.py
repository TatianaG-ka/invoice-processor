"""Redis-backed idempotency for write endpoints.

Why Redis (not a Postgres unique constraint):

* Decision is sub-millisecond on the cache hit — KSeF invoices arrive
  in bursts (n8n batches, retried HTTP timeouts), and a duplicate from
  a retry should not pay the full parse + INSERT path before the DB
  rejects it.
* TTL ergonomics: a KSeF invoice number is forever-unique per seller,
  but the *retry window* we care about for dedup is hours-to-a-day.
  A 24h TTL keeps the keyspace bounded and avoids accumulating dead
  keys for archived invoices.
* Best-effort by design: a Redis outage degrades to "no dedup", not
  "no service". The failure is logged loudly so the operator knows
  duplicate writes are momentarily possible.

Production points :data:`Settings.IDEMPOTENCY_REDIS_URL` at a managed
Redis (e.g. Upstash). Tests + CI fall back to ``REDIS_URL`` (which is
a fakeredis async client under the autouse fixture).
"""

from __future__ import annotations

import logging
from typing import Final

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.config import settings

logger = logging.getLogger(__name__)

KEY_PREFIX: Final[str] = "invoice-processor:idempotency"
DEFAULT_TTL_SECONDS: Final[int] = 86_400  # 24 hours

_client: aioredis.Redis | None = None


def _resolve_url() -> str:
    """Return the URL the idempotency client should connect to.

    ``IDEMPOTENCY_REDIS_URL`` is preferred when set, so production can
    point this at a managed Redis (Upstash) without dragging the queue
    along; otherwise we fall back to ``REDIS_URL``.
    """
    return settings.IDEMPOTENCY_REDIS_URL or settings.REDIS_URL


def get_client() -> aioredis.Redis:
    """Return the lazily-built async Redis client.

    Lazy so importing this module never opens a TCP connection — tests
    monkeypatch ``_client`` with a fakeredis async client before any
    call. One client per process, one connection pool, same shape as
    :func:`app.queue.connection.get_redis`.
    """
    global _client
    if _client is None:
        _client = aioredis.from_url(_resolve_url(), decode_responses=True)
    return _client


def reset() -> None:
    """Drop the cached singleton.

    Used by tests that swap ``settings.IDEMPOTENCY_REDIS_URL`` between
    cases. Production never calls this — the client lives for the
    lifetime of the process.
    """
    global _client
    _client = None


def ksef_key(seller_nip: str, invoice_number: str) -> str:
    """Build the canonical idempotency key for a KSeF ingest.

    Polish tax law guarantees ``(seller_nip, invoice_number)`` is
    globally unique — a seller cannot reuse a number across years.
    Keys are kept human-readable (no hashing) so they are easy to
    inspect in a Redis CLI when debugging a duplicate complaint.
    """
    return f"{KEY_PREFIX}:ksef:{seller_nip}:{invoice_number}"


async def find_existing(key: str) -> int | None:
    """Look up the ``invoice_id`` previously stored under ``key``.

    Returns ``None`` when the key is missing **or** when Redis errors —
    callers must treat ``None`` as "no cached duplicate, proceed", so a
    Redis outage degrades to "no dedup", not "no service".
    """
    client = get_client()
    try:
        raw = await client.get(key)
    except RedisError:
        logger.warning(
            "Idempotency lookup failed for key=%s; proceeding without dedup",
            key,
            exc_info=True,
        )
        return None
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Idempotency key %s holds non-integer payload %r — ignoring",
            key,
            raw,
        )
        return None


async def claim(
    key: str,
    invoice_id: int,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    """Record ``invoice_id`` under ``key`` with the given TTL.

    Best-effort — the row is already saved by the time we get here,
    so a Redis failure means a future duplicate of the same XML will
    parse and save again (DB will hold two rows). Logged so the gap
    is visible during the outage window.
    """
    client = get_client()
    try:
        await client.set(key, str(invoice_id), ex=ttl_seconds)
    except RedisError:
        logger.warning(
            "Idempotency claim failed for key=%s — duplicate writes possible "
            "until Redis recovers",
            key,
            exc_info=True,
        )
