"""End-to-end tests for ``GET /invoices/stats``.

Seeds invoices directly via the repository (bypassing the LLM
categorisation path) so we control the (category, total_gross) pairs
the aggregation should bucket. Then we hit the HTTP endpoint and
assert the returned counts and sums match.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Invoice


def _make_invoice(
    *,
    seller_name: str = "Acme Sp. z o.o.",
    total_gross: Decimal,
    currency: str = "PLN",
    category: str | None = None,
    created_at: datetime | None = None,
) -> Invoice:
    return Invoice(
        invoice_number=None,
        issue_date=None,
        seller_name=seller_name,
        seller_nip=None,
        seller_address=None,
        buyer_name="Buyer Co.",
        buyer_nip=None,
        buyer_address=None,
        total_net=total_gross,
        total_vat=Decimal("0"),
        total_gross=total_gross,
        currency=currency,
        line_items=[],
        category=category,
        category_confidence=None,
        created_at=created_at or datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Empty DB
# ---------------------------------------------------------------------------


def test_stats_empty_db_returns_zeros(client: TestClient):
    response = client.get("/invoices/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["period_days"] == 30
    assert body["currency"] == "PLN"
    assert body["total_invoices"] == 0
    assert body["grand_total_gross"] == "0"
    assert body["by_category"] == []


# ---------------------------------------------------------------------------
# Aggregation correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_aggregates_counts_and_sums_per_category(
    client: TestClient, db_session: AsyncSession
):
    """Two invoices in the same category should collapse into one bucket."""
    db_session.add(
        _make_invoice(total_gross=Decimal("100.00"), category="Usługi IT i oprogramowanie")
    )
    db_session.add(
        _make_invoice(total_gross=Decimal("250.50"), category="Usługi IT i oprogramowanie")
    )
    db_session.add(_make_invoice(total_gross=Decimal("80.00"), category="Materiały biurowe"))
    db_session.add(_make_invoice(total_gross=Decimal("40.00"), category=None))
    await db_session.commit()

    response = client.get("/invoices/stats")

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total_invoices"] == 4
    assert Decimal(body["grand_total_gross"]) == Decimal("470.50")

    buckets = {row["category"]: row for row in body["by_category"]}
    assert buckets["Usługi IT i oprogramowanie"]["count"] == 2
    assert Decimal(buckets["Usługi IT i oprogramowanie"]["total_gross"]) == Decimal("350.50")
    assert buckets["Materiały biurowe"]["count"] == 1
    assert buckets[None]["count"] == 1  # un-categorised bucket surfaces


@pytest.mark.asyncio
async def test_stats_period_window_excludes_old_rows(client: TestClient, db_session: AsyncSession):
    """Rows older than ``period_days`` must not contribute to totals."""
    fresh = datetime.now(UTC) - timedelta(days=5)
    stale = datetime.now(UTC) - timedelta(days=45)
    db_session.add(_make_invoice(total_gross=Decimal("100.00"), category="A", created_at=fresh))
    db_session.add(_make_invoice(total_gross=Decimal("9999.00"), category="A", created_at=stale))
    await db_session.commit()

    response = client.get("/invoices/stats?period_days=30")

    assert response.status_code == 200
    body = response.json()
    assert body["total_invoices"] == 1
    assert Decimal(body["grand_total_gross"]) == Decimal("100.00")


@pytest.mark.asyncio
async def test_stats_currency_filter_excludes_other_currencies(
    client: TestClient, db_session: AsyncSession
):
    db_session.add(_make_invoice(total_gross=Decimal("100.00"), currency="PLN", category="A"))
    db_session.add(_make_invoice(total_gross=Decimal("500.00"), currency="EUR", category="A"))
    await db_session.commit()

    pln = client.get("/invoices/stats?currency=PLN").json()
    eur = client.get("/invoices/stats?currency=EUR").json()

    assert pln["total_invoices"] == 1
    assert Decimal(pln["grand_total_gross"]) == Decimal("100.00")
    assert eur["total_invoices"] == 1
    assert Decimal(eur["grand_total_gross"]) == Decimal("500.00")


@pytest.mark.asyncio
async def test_stats_currency_query_is_uppercased(client: TestClient, db_session: AsyncSession):
    db_session.add(_make_invoice(total_gross=Decimal("100.00"), currency="PLN"))
    await db_session.commit()

    response = client.get("/invoices/stats?currency=pln")

    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "PLN"
    assert body["total_invoices"] == 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("period_days", [0, -1, 366, 10000])
def test_stats_rejects_period_outside_window(client: TestClient, period_days: int):
    response = client.get(f"/invoices/stats?period_days={period_days}")
    assert response.status_code == 400


@pytest.mark.parametrize("currency", ["", "PL", "PLNX", "12X", "$$$"])
def test_stats_rejects_invalid_currency(client: TestClient, currency: str):
    response = client.get(f"/invoices/stats?currency={currency}")
    assert response.status_code == 400
