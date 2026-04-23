"""Redis + RQ queue bootstrap.

The Redis client and the :class:`rq.Queue` are built **lazily** so that

1. Importing :mod:`app.queue.connection` during module discovery (at
   FastAPI startup, before any request) does not open a TCP connection
   to a Redis that may not be reachable yet.
2. Tests can monkeypatch ``_redis_client`` / ``_queue`` with fakeredis
   + a synchronous :class:`~rq.Queue` before the first real call, so
   enqueued jobs execute inline and no worker process is needed.

The same Redis client is reused for both enqueueing (``Queue.enqueue``)
and job-status lookups (``rq.job.Job.fetch``).
"""

from __future__ import annotations

from redis import Redis
from rq import Queue

from app.config import settings

DEFAULT_QUEUE_NAME = "default"
"""Single-queue topology for the portfolio scope — one queue, one worker
class. Named explicitly so the worker invocation
(``rq worker ... default``) and the enqueue side agree by string."""


_redis_client: Redis | None = None
_queue: Queue | None = None


def get_redis() -> Redis:
    """Return the lazily-constructed Redis client.

    Reads ``settings.REDIS_URL``; tests override via :func:`set_redis`
    or by patching the module attribute directly.
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.REDIS_URL)
    return _redis_client


def get_queue() -> Queue:
    """Return the lazily-constructed default RQ queue.

    The queue shares the same Redis client returned by
    :func:`get_redis`, so job lookups (``Job.fetch(id, connection=...)``)
    see the same data the enqueue call wrote.
    """
    global _queue
    if _queue is None:
        _queue = Queue(DEFAULT_QUEUE_NAME, connection=get_redis())
    return _queue


def queue_dependency() -> Queue:
    """FastAPI dependency that resolves to the shared default queue.

    Routes depend on this (not on :func:`get_queue` directly) so that
    ``app.dependency_overrides[queue_dependency] = ...`` in tests
    replaces the queue without reaching into module globals.
    """
    return get_queue()


def reset() -> None:
    """Drop the cached singletons.

    Used by tests that swap ``settings.REDIS_URL`` or install a
    fakeredis-backed queue between cases.
    """
    global _redis_client, _queue
    _redis_client = None
    _queue = None
