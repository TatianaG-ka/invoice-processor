"""Unit tests for :mod:`app.services.ksef_parser`.

Covers schema detection, both parse paths (FA(2) + FA(3)), and the
invalid-input paths that FastAPI relies on to decide HTTP status.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from lxml import etree

from app.services.ksef_parser import (
    FA2_NAMESPACE,
    FA3_NAMESPACE,
    KSeFParseError,
    _detect_schema,
    parse_ksef,
)

# ---------------------------------------------------------------------------
# Schema detection.
# ---------------------------------------------------------------------------


def test_detect_schema_fa2(ksef_fa2_bytes: bytes):
    root = etree.fromstring(ksef_fa2_bytes)
    assert _detect_schema(root) == "FA2"


def test_detect_schema_fa3(ksef_fa3_bytes: bytes):
    root = etree.fromstring(ksef_fa3_bytes)
    assert _detect_schema(root) == "FA3"


def test_detect_schema_unknown_namespace_raises():
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Faktura xmlns="http://example.com/unknown">'
        b"</Faktura>"
    )
    root = etree.fromstring(xml)
    with pytest.raises(KSeFParseError, match="Unsupported KSeF namespace"):
        _detect_schema(root)


def test_detect_schema_wrong_root_element_raises():
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<NotFaktura xmlns="' + FA3_NAMESPACE.encode() + b'"></NotFaktura>'
    )
    root = etree.fromstring(xml)
    with pytest.raises(KSeFParseError, match="Root element must be 'Faktura'"):
        _detect_schema(root)


# ---------------------------------------------------------------------------
# FA(2) — happy path.
# ---------------------------------------------------------------------------


def test_parse_fa2_returns_invoice_with_all_fields(ksef_fa2_bytes: bytes):
    invoice = parse_ksef(ksef_fa2_bytes)

    assert invoice.invoice_number == "FV/FA2/001/2026"
    assert invoice.issue_date == date(2026, 1, 15)
    assert invoice.seller.name == "Acme Sp. z o.o."
    assert invoice.seller.nip == "0000000000"
    assert invoice.buyer.name == "Kontrahent S.A."
    assert invoice.buyer.nip == "1111111111"
    assert invoice.totals.currency == "PLN"
    assert invoice.totals.net == Decimal("1000.00")
    assert invoice.totals.vat == Decimal("230.00")
    assert invoice.totals.gross == Decimal("1230.00")


def test_parse_fa2_parses_all_line_items(ksef_fa2_bytes: bytes):
    invoice = parse_ksef(ksef_fa2_bytes)

    assert len(invoice.line_items) == 2

    first = invoice.line_items[0]
    assert first.description == "Usluga konsultingowa"
    assert first.quantity == Decimal("1")
    assert first.unit_price == Decimal("1000.00")
    assert first.total == Decimal("1000.00")

    second = invoice.line_items[1]
    assert second.description == "Wsparcie techniczne"
    assert second.quantity == Decimal("2")


def test_parse_fa2_address_is_single_line(ksef_fa2_bytes: bytes):
    """AdresL1 + AdresL2 are flattened with comma separator."""
    invoice = parse_ksef(ksef_fa2_bytes)
    assert invoice.seller.address == "ul. Przykladowa 1, 00-001 Warszawa"


# ---------------------------------------------------------------------------
# FA(3) — happy path.
# ---------------------------------------------------------------------------


def test_parse_fa3_returns_invoice_with_all_fields(ksef_fa3_bytes: bytes):
    invoice = parse_ksef(ksef_fa3_bytes)

    assert invoice.invoice_number == "FV/FA3/042/2026"
    assert invoice.issue_date == date(2026, 3, 20)
    assert invoice.seller.name == "Nowa Firma Sp. z o.o."
    assert invoice.buyer.name == "Klient Premium Sp. z o.o."
    assert invoice.totals.gross == Decimal("6150.00")


def test_parse_fa3_single_line_item(ksef_fa3_bytes: bytes):
    invoice = parse_ksef(ksef_fa3_bytes)
    assert len(invoice.line_items) == 1
    assert invoice.line_items[0].description == "Licencja oprogramowania (rocznie)"


# ---------------------------------------------------------------------------
# Error paths.
# ---------------------------------------------------------------------------


def test_parse_ksef_empty_bytes_raises():
    with pytest.raises(KSeFParseError, match="Empty"):
        parse_ksef(b"")


def test_parse_ksef_malformed_xml_raises():
    with pytest.raises(KSeFParseError, match="Malformed"):
        parse_ksef(b"<not-xml>unterminated")


def test_parse_ksef_missing_podmiot1_raises():
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Faktura xmlns="' + FA3_NAMESPACE.encode() + b'">'
        b"<Naglowek/>"
        b"</Faktura>"
    )
    with pytest.raises(KSeFParseError, match="Podmiot1"):
        parse_ksef(xml)


def test_parse_ksef_missing_seller_name_raises(ksef_fa3_bytes: bytes):
    """Nazwa under DaneIdentyfikacyjne is required."""
    corrupted = ksef_fa3_bytes.replace(
        b"<Nazwa>Nowa Firma Sp. z o.o.</Nazwa>",
        b"<Nazwa></Nazwa>",
        1,
    )
    with pytest.raises(KSeFParseError, match="Nazwa"):
        parse_ksef(corrupted)


def test_parse_ksef_is_not_vulnerable_to_xxe():
    """Entity resolution is disabled — external DTDs must not fetch."""
    xxe_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<Faktura xmlns="' + FA3_NAMESPACE.encode() + b'">'
        b"<Podmiot1><DaneIdentyfikacyjne><Nazwa>&xxe;</Nazwa></DaneIdentyfikacyjne></Podmiot1>"
        b"</Faktura>"
    )
    # Either the parser refuses the DTD outright (KSeFParseError) or it
    # parses but the entity resolves to an empty string — both outcomes
    # are acceptable; what we must never see is the file contents.
    try:
        invoice = parse_ksef(xxe_xml)
    except KSeFParseError:
        return
    assert "root:" not in (invoice.seller.name or "")


# ---------------------------------------------------------------------------
# Namespace constants are what we document.
# ---------------------------------------------------------------------------


def test_namespace_constants_match_gov_pl_schemas():
    """Guard against accidental namespace edits that would break real KSeF."""
    assert FA2_NAMESPACE == "http://crd.gov.pl/wzor/2023/06/29/12648/"
    assert FA3_NAMESPACE == "http://crd.gov.pl/wzor/2025/06/25/13775/"
