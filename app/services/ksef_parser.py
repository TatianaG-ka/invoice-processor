"""KSeF invoice XML parser — dual schema (FA(2) + FA(3)).

KSeF (Krajowy System e-Faktur) is the Polish national e-invoicing
system. Two XSD schema versions are currently in use:

* **FA(2)** — legacy namespace ``http://crd.gov.pl/wzor/2023/06/29/12648/``.
  In use for invoices issued before 2026-02-01 and for voluntary KSeF
  adopters during the transition period.
* **FA(3)** — current namespace ``http://crd.gov.pl/wzor/2025/06/25/13775/``.
  Mandatory for all VAT-registered taxpayers from 2026-02-01.

The public entry point :func:`parse_ksef` detects the schema via the
document root's namespace and dispatches to the matching parser. The
two parsers share most of their logic because the body structure
(``Podmiot1``, ``Podmiot2``, ``Fa``, ``FaWiersz``) is stable between
versions — the dispatch exists so we can cleanly accommodate future
FA(3) additions (e.g. mandatory ``IDNabywcy`` tagging) without
branching inside one large function.

All schema differences are expressed as namespace-prefixed XPath
queries, so adding an FA(4) parser in the future is a one-function
change with no impact on existing code paths.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from lxml import etree

from app.schemas.invoice import ExtractedInvoice, LineItem, Party, Totals

FA2_NAMESPACE = "http://crd.gov.pl/wzor/2023/06/29/12648/"
FA3_NAMESPACE = "http://crd.gov.pl/wzor/2025/06/25/13775/"

_SUPPORTED_NAMESPACES: dict[str, Literal["FA2", "FA3"]] = {
    FA2_NAMESPACE: "FA2",
    FA3_NAMESPACE: "FA3",
}


class KSeFParseError(ValueError):
    """Raised when a KSeF XML document cannot be parsed.

    Inherits from :class:`ValueError` so FastAPI's error-mapping in
    :mod:`app.main` treats it as a client-side 422 by default, while
    callers that want to distinguish KSeF-specific failures can still
    ``except KSeFParseError``.
    """


def parse_ksef(xml_bytes: bytes) -> ExtractedInvoice:
    """Parse a KSeF invoice XML into :class:`ExtractedInvoice`.

    Raises :class:`KSeFParseError` for unparseable bytes, unsupported
    schema versions, or structurally invalid documents.
    """
    if not xml_bytes:
        raise KSeFParseError("Empty XML payload.")

    try:
        # ``resolve_entities=False`` blocks entity-expansion attacks
        # (billion-laughs, XXE) that lxml enables by default.
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.fromstring(xml_bytes, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise KSeFParseError(f"Malformed XML: {exc}") from exc

    schema = _detect_schema(root)
    if schema == "FA2":
        return _parse_fa2(root)
    return _parse_fa3(root)


def _detect_schema(root: etree._Element) -> Literal["FA2", "FA3"]:
    """Identify the KSeF schema version from the root namespace."""
    qname = etree.QName(root.tag)
    if qname.localname != "Faktura":
        raise KSeFParseError(f"Root element must be 'Faktura', got {qname.localname!r}.")
    namespace = qname.namespace
    schema = _SUPPORTED_NAMESPACES.get(namespace or "")
    if schema is None:
        raise KSeFParseError(
            f"Unsupported KSeF namespace: {namespace!r}. "
            f"Supported: FA(2)={FA2_NAMESPACE}, FA(3)={FA3_NAMESPACE}."
        )
    return schema


# ---------------------------------------------------------------------------
# Schema-specific parsers.
# ---------------------------------------------------------------------------
#
# The two functions look nearly identical today — FA(3) re-uses the
# FA(2) body layout. Kept separate so that when FA(3)-only features
# (e.g. KSeF number, correction invoice metadata) need reading, the
# divergence stays localised.


def _parse_fa2(root: etree._Element) -> ExtractedInvoice:
    return _parse_common(root, namespace=FA2_NAMESPACE)


def _parse_fa3(root: etree._Element) -> ExtractedInvoice:
    return _parse_common(root, namespace=FA3_NAMESPACE)


def _parse_common(root: etree._Element, namespace: str) -> ExtractedInvoice:
    """Shared body parsing — current FA(2)/FA(3) structural overlap."""
    ns = {"fa": namespace}

    seller = _parse_party(root, "Podmiot1", ns)
    buyer = _parse_party(root, "Podmiot2", ns)

    fa = _require_one(root, "fa:Fa", ns, "<Fa> element")

    invoice_number = _find_text(fa, "fa:P_2", ns)
    issue_date = _parse_date(_find_text(fa, "fa:P_1", ns))
    currency = _find_text(fa, "fa:KodWaluty", ns) or "PLN"

    net = _parse_decimal(_find_text(fa, "fa:P_13_1", ns), default=Decimal("0.00"))
    vat = _parse_decimal(_find_text(fa, "fa:P_14_1", ns), default=Decimal("0.00"))
    gross = _parse_decimal(_find_text(fa, "fa:P_15", ns), default=Decimal("0.00"))

    line_items = [_parse_line_item(el, ns) for el in fa.findall("fa:FaWiersz", ns)]

    return ExtractedInvoice(
        invoice_number=invoice_number,
        issue_date=issue_date,
        seller=seller,
        buyer=buyer,
        line_items=line_items,
        totals=Totals(net=net, vat=vat, gross=gross, currency=currency),
    )


def _parse_party(root: etree._Element, tag: str, ns: dict[str, str]) -> Party:
    party = _require_one(root, f"fa:{tag}", ns, f"<{tag}> element")
    name = _find_text(party, "fa:DaneIdentyfikacyjne/fa:Nazwa", ns)
    nip = _find_text(party, "fa:DaneIdentyfikacyjne/fa:NIP", ns)
    address = _compose_address(party, ns)

    if not name:
        raise KSeFParseError(f"<{tag}/DaneIdentyfikacyjne/Nazwa> is required.")

    return Party(name=name, nip=nip, address=address)


def _compose_address(party: etree._Element, ns: dict[str, str]) -> str | None:
    """Flatten ``<Adres>`` into a single-line string.

    ``ExtractedInvoice.Party.address`` is a free-form string shared
    with the LLM-based extractor; flattening here keeps the domain
    model homogeneous regardless of source (PDF vs XML).
    """
    lines: list[str] = []
    for xpath in ("fa:Adres/fa:AdresL1", "fa:Adres/fa:AdresL2"):
        text = _find_text(party, xpath, ns)
        if text:
            lines.append(text)
    return ", ".join(lines) if lines else None


def _parse_line_item(el: etree._Element, ns: dict[str, str]) -> LineItem:
    description = _find_text(el, "fa:P_7", ns) or ""
    quantity = _parse_decimal(_find_text(el, "fa:P_8B", ns), default=Decimal("0"))
    unit_price = _parse_decimal(_find_text(el, "fa:P_9A", ns), default=Decimal("0"))
    total = _parse_decimal(_find_text(el, "fa:P_11", ns), default=Decimal("0"))
    return LineItem(
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        total=total,
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _require_one(
    root: etree._Element,
    xpath: str,
    ns: dict[str, str],
    label: str,
) -> etree._Element:
    found = root.find(xpath, ns)
    if found is None:
        raise KSeFParseError(f"{label} is required but missing.")
    return found


def _find_text(root: etree._Element, xpath: str, ns: dict[str, str]) -> str | None:
    el = root.find(xpath, ns)
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _parse_decimal(text: str | None, *, default: Decimal) -> Decimal:
    """Parse a KSeF numeric field, defaulting when absent.

    KSeF numbers use dot decimal separator (no thousand separators),
    so the permissive handling is defensive — real schemas enforce
    XSD ``decimal`` and would never return Polish-style formatting.
    """
    if text is None:
        return default
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation as exc:
        raise KSeFParseError(f"Not a decimal value: {text!r}") from exc


def _parse_date(text: str | None) -> date | None:
    """Parse ``YYYY-MM-DD``; return ``None`` on absent/malformed."""
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
