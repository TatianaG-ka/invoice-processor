"""Qdrant-backed vector store for semantic invoice search.

Phase 6 wiring:

* On every invoice save (PDF pipeline in :mod:`app.queue.tasks`, KSeF
  route in :mod:`app.main`), we embed a short representation of the
  invoice and upsert it as a point with ``id = invoice_id``. Using the
  DB primary key as the Qdrant point id means re-indexing the same
  invoice is a natural upsert (no orphan points).
* ``GET /invoices/search`` embeds the query, asks Qdrant for top-K,
  then hydrates the hits from Postgres. We never serve search results
  from the Qdrant payload alone — the DB is the source of truth.

The client is constructed **lazily** (same pattern as :mod:`app.queue.connection`)
so importing this module does not open a TCP connection or try to hit a
Qdrant that may not be up yet. Tests monkeypatch the module-level
singleton with an in-memory ``QdrantClient(":memory:")`` instance.
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams

from app.config import settings
from app.services import embedder
from app.services.embedder import EMBEDDING_DIM

logger = logging.getLogger(__name__)


class VectorStore:
    """Thin wrapper around :class:`QdrantClient` scoped to one collection.

    Single-collection scope matches the portfolio: one collection
    (``invoices``), one vector per invoice. If we later need multi-modal
    indexing (e.g. line-item-level vectors), a second store instance
    against a second collection is the natural extension.
    """

    def __init__(self, client: QdrantClient, collection: str) -> None:
        self._client = client
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    def ensure_collection(self) -> None:
        """Create the collection if missing. Idempotent.

        Called once on first access via :func:`get_store` — not on every
        upsert, because ``collection_exists`` is an extra RTT and
        subsequent calls would pay it for nothing.
        """
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection %r (dim=%d)", self._collection, EMBEDDING_DIM)

    def upsert(self, invoice_id: int, vector: list[float], payload: dict[str, Any]) -> None:
        """Upsert a single invoice vector keyed by its DB primary key.

        Using ``invoice_id`` as the point id keeps Qdrant and Postgres
        trivially joinable and makes re-indexing (e.g. after a model
        swap) a no-orphan operation.
        """
        point = PointStruct(id=invoice_id, vector=vector, payload=payload)
        self._client.upsert(collection_name=self._collection, points=[point])

    def search(self, query_vector: list[float], limit: int = 10) -> list[tuple[int, float]]:
        """Return ``(invoice_id, score)`` tuples ordered by cosine similarity.

        Scores are cosine similarities in ``[-1, 1]``; higher is better.
        The caller is responsible for hydrating the invoice rows from
        the DB — this layer only speaks point ids.
        """
        hits = self._client.search(
            collection_name=self._collection,
            query_vector=query_vector,
            limit=limit,
        )
        return [(int(hit.id), float(hit.score)) for hit in hits]


_client: QdrantClient | None = None
_store: VectorStore | None = None


def get_store() -> VectorStore:
    """Return the lazily-constructed vector store singleton.

    Reads ``settings.QDRANT_URL`` + ``settings.QDRANT_COLLECTION`` and
    ensures the collection exists on first call. Tests monkeypatch
    ``_client`` / ``_store`` with in-memory equivalents before any call.
    """
    global _client, _store
    if _store is None:
        _client = QdrantClient(url=settings.QDRANT_URL)
        _store = VectorStore(client=_client, collection=settings.QDRANT_COLLECTION)
        _store.ensure_collection()
    return _store


def vector_store_dependency() -> VectorStore:
    """FastAPI dependency resolving to the shared vector store.

    Routes depend on this (not :func:`get_store` directly) so that
    ``app.dependency_overrides[vector_store_dependency] = ...`` in
    tests replaces the store without reaching into module globals.
    """
    return get_store()


def reset() -> None:
    """Drop the cached singletons.

    Used by tests that swap ``settings.QDRANT_URL`` or install an
    in-memory store between cases.
    """
    global _client, _store
    _client = None
    _store = None


def build_invoice_text(extracted: Any) -> str:
    """Compose the short text representation we embed for an invoice.

    Uses seller name + line-item descriptions — these are the
    human-meaningful free-text fields most useful for "find invoices
    about X" style queries. Line items are the majority of signal;
    totals and dates are structured, not textual, and belong to
    structured filters rather than semantic search.

    Accepts any object exposing ``.seller.name`` and ``.line_items``
    (either :class:`~app.schemas.invoice.ExtractedInvoice` or the
    persisted variant) — ``Any`` rather than a concrete type to avoid
    a circular import with the schemas module.
    """
    descriptions = " ".join(item.description for item in extracted.line_items)
    return f"{extracted.seller.name} | {descriptions}".strip(" |")


def build_invoice_payload(invoice_id: int, extracted: Any) -> dict[str, Any]:
    """Minimal Qdrant payload: enough for debugging, not a DB substitute.

    Search results are always rehydrated from Postgres; the payload
    exists so a Qdrant-only inspection (dashboard, debugging session)
    can tell which invoice a point belongs to without cross-referencing
    the DB.
    """
    return {
        "invoice_id": invoice_id,
        "invoice_number": extracted.invoice_number,
        "seller_name": extracted.seller.name,
    }


def index_invoice(
    invoice_id: int,
    extracted: Any,
    store: VectorStore | None = None,
) -> bool:
    """Embed ``extracted`` and upsert into Qdrant; best-effort.

    Returns ``True`` on success, ``False`` on any failure (logged). The
    caller NEVER branches on the return value to decide whether the
    invoice was saved — by the time this is invoked, the DB row already
    exists. The boolean exists only so tests can assert the happy path
    and so a future caller could surface a "indexing lag" flag to the
    client if that becomes useful.

    Deliberately broad ``except Exception``: Qdrant client errors, the
    embedder downloading a model from an offline host, network blips —
    all of these should degrade search coverage, not break the write
    path. The DB is the system of record and an un-indexed invoice can
    be reindexed later from a simple script.
    """
    try:
        actual_store = store or get_store()
        text = build_invoice_text(extracted)
        if not text:
            logger.warning("Invoice id=%d has no embeddable text; skipping index", invoice_id)
            return False
        vector = embedder.embed(text)
        payload = build_invoice_payload(invoice_id, extracted)
        actual_store.upsert(invoice_id, vector, payload)
        logger.info("Indexed invoice id=%d into Qdrant", invoice_id)
        return True
    except Exception:  # noqa: BLE001 — intentional broad catch (see docstring)
        logger.exception(
            "Failed to index invoice id=%d; continuing without search coverage",
            invoice_id,
        )
        return False
