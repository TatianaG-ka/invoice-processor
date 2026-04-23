"""Unit tests for :mod:`app.services.vector_store`.

Exercise the Qdrant wrapper directly against ``QdrantClient(":memory:")``
— no embedder, no FastAPI, just upsert + search. The conftest fixture
still stubs the embedder module-wide, but these tests hand-craft
vectors so they can make precise ordering assertions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from qdrant_client import QdrantClient

from app.schemas.invoice import ExtractedInvoice, LineItem, Party, Totals
from app.services.embedder import EMBEDDING_DIM
from app.services.vector_store import (
    VectorStore,
    build_invoice_payload,
    build_invoice_text,
    index_invoice,
)


def _unit_vector(dominant_index: int) -> list[float]:
    """Return a 384-dim unit vector with a single spike at ``dominant_index``.

    These are maximally distinguishable under cosine similarity: two
    distinct spikes have similarity 0, the same spike has similarity 1.
    """
    vec = [0.0] * EMBEDDING_DIM
    vec[dominant_index] = 1.0
    return vec


def _make_invoice(seller_name: str, descriptions: list[str]) -> ExtractedInvoice:
    return ExtractedInvoice(
        invoice_number="INV-001",
        issue_date=None,
        seller=Party(name=seller_name),
        buyer=Party(name="Buyer Sp. z o.o."),
        line_items=[
            LineItem(
                description=desc,
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                total=Decimal("100.00"),
            )
            for desc in descriptions
        ],
        totals=Totals(
            net=Decimal("100.00"),
            vat=Decimal("23.00"),
            gross=Decimal("123.00"),
        ),
    )


class TestVectorStoreBasics:
    def test_ensure_collection_is_idempotent(self):
        """Second call must not raise or replace the collection."""
        client = QdrantClient(":memory:")
        store = VectorStore(client=client, collection="test-idem")
        store.ensure_collection()
        store.ensure_collection()  # no error
        assert client.collection_exists("test-idem")

    def test_upsert_then_search_returns_same_point(self, test_vector_store: VectorStore):
        vec = _unit_vector(5)
        test_vector_store.upsert(invoice_id=42, vector=vec, payload={"invoice_id": 42})
        hits = test_vector_store.search(query_vector=vec, limit=10)
        assert hits, "Expected at least one hit after upsert"
        assert hits[0][0] == 42
        assert hits[0][1] == pytest.approx(1.0)

    def test_search_orders_by_similarity(self, test_vector_store: VectorStore):
        test_vector_store.upsert(1, _unit_vector(0), {"invoice_id": 1})
        test_vector_store.upsert(2, _unit_vector(1), {"invoice_id": 2})

        # Query vector pointing mostly at index 0 — point 1 should win.
        query = [0.0] * EMBEDDING_DIM
        query[0] = 0.9
        query[1] = 0.1
        hits = test_vector_store.search(query_vector=query, limit=10)

        assert [h[0] for h in hits] == [1, 2]
        assert hits[0][1] > hits[1][1]

    def test_upsert_is_really_upsert(self, test_vector_store: VectorStore):
        """Same id with new vector replaces the old point (no duplicates)."""
        test_vector_store.upsert(7, _unit_vector(0), {"v": 1})
        test_vector_store.upsert(7, _unit_vector(1), {"v": 2})

        hits = test_vector_store.search(_unit_vector(1), limit=10)
        # Only one point for id=7, now pointing at index 1.
        assert len(hits) == 1
        assert hits[0][0] == 7


class TestHelpers:
    def test_build_invoice_text_joins_seller_and_descriptions(self):
        invoice = _make_invoice(
            "Tesla Sp. z o.o.", ["Model Y konfiguracja", "Opłata rejestracyjna"]
        )
        text = build_invoice_text(invoice)
        assert "Tesla Sp. z o.o." in text
        assert "Model Y konfiguracja" in text
        assert "Opłata rejestracyjna" in text

    def test_build_invoice_text_without_line_items(self):
        invoice = _make_invoice("Acme", [])
        text = build_invoice_text(invoice)
        assert text == "Acme"

    def test_build_invoice_payload_is_minimal(self):
        invoice = _make_invoice("Acme", ["widget"])
        payload = build_invoice_payload(123, invoice)
        assert payload == {
            "invoice_id": 123,
            "invoice_number": "INV-001",
            "seller_name": "Acme",
        }


class TestReindexAll:
    """Cold-start reindex: rebuild Qdrant from Postgres on container boot.

    Exercised end-to-end via the KSeF upload → reset the vector store
    → reindex_all → search still finds the invoice. Asserts the whole
    Postgres → build_invoice_text → embed → upsert chain works without
    any of the usual FastAPI or queue plumbing.
    """

    async def test_reindex_is_noop_on_empty_db(self, test_vector_store: VectorStore):
        from app.services.vector_store import reindex_all

        assert await reindex_all(store=test_vector_store) == 0

    async def test_reindex_rebuilds_from_saved_invoices(
        self,
        client,
        ksef_fa2_bytes: bytes,
        test_vector_store: VectorStore,
    ):
        # 1. Save an invoice through the normal path (populates Postgres + Qdrant).
        response = client.post(
            "/invoices/ksef",
            files={"file": ("fa2.xml", ksef_fa2_bytes, "application/xml")},
        )
        assert response.status_code == 201
        invoice_id = response.json()["id"]

        # 2. Simulate a Cloud Run instance swap: the vector store is fresh
        #    but Postgres still has the row.
        fresh_client = QdrantClient(":memory:")
        collection = f"reindex-{invoice_id}"
        fresh_store = VectorStore(client=fresh_client, collection=collection)
        fresh_store.ensure_collection()
        assert fresh_store.search(_unit_vector(0), limit=10) == []

        # 3. Reindex must repopulate the fresh store from the DB.
        from app.services.vector_store import reindex_all

        count = await reindex_all(store=fresh_store)
        assert count == 1

        # 4. The rebuilt store must now be able to serve queries.
        from app.services import embedder

        vector = embedder.embed("Acme Sp. z o.o.")
        hits = fresh_store.search(vector, limit=10)
        assert hits, "Expected reindexed invoice to be searchable"
        assert hits[0][0] == invoice_id


class TestIndexInvoice:
    def test_success_returns_true_and_point_is_searchable(self, test_vector_store: VectorStore):
        invoice = _make_invoice("Drukarnia XYZ", ["Toner HP LaserJet"])
        assert index_invoice(invoice_id=55, extracted=invoice, store=test_vector_store) is True

        # Round-trip: searching the same text returns the point.
        from app.services import embedder

        query_vec = embedder.embed("Drukarnia XYZ | Toner HP LaserJet")
        hits = test_vector_store.search(query_vec, limit=10)
        assert hits[0][0] == 55

    def test_empty_text_is_skipped_not_raised(self, test_vector_store: VectorStore):
        """An invoice with no seller name AND no line items has nothing to embed."""
        invoice = ExtractedInvoice(
            seller=Party(name=""),
            buyer=Party(name="Buyer"),
            totals=Totals(net=Decimal("0"), vat=Decimal("0"), gross=Decimal("0")),
        )
        assert index_invoice(invoice_id=1, extracted=invoice, store=test_vector_store) is False

    def test_failure_is_swallowed_and_returns_false(self, monkeypatch):
        """Broken store must not propagate — save path has to stay green."""
        invoice = _make_invoice("Acme", ["widget"])

        class BrokenStore:
            def upsert(self, *args, **kwargs):
                raise RuntimeError("qdrant unreachable")

        assert index_invoice(invoice_id=9, extracted=invoice, store=BrokenStore()) is False
