"""LLM-driven extraction of structured data from invoice text.

The service accepts the raw text yielded by
:func:`app.services.pdf_text_extractor.extract_text` and returns a
strongly-typed :class:`~app.schemas.invoice.ExtractedInvoice`.

Two modes coexist:

* **Real mode** — calls OpenAI with Structured Outputs (strict JSON
  schema). Active whenever an API key is configured and
  ``EXTRACTOR_STRATEGY`` is ``"openai"``.
* **Mock mode** — returns a deterministic stub. Active whenever the
  API key is blank or the strategy is ``"mock"``. This keeps CI
  green without a secret and lets the rest of the pipeline (DB,
  Qdrant, endpoints) be developed against a stable payload.

Retry policy: transient OpenAI errors are retried up to three times
with exponential backoff (``tenacity``). Any non-transient failure is
surfaced as :class:`InvoiceExtractionError`.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.schemas.invoice import (
    ExtractedInvoice,
    LineItem,
    LLMInvoiceResponse,
    Party,
    Totals,
    from_llm_response,
)

logger = logging.getLogger(__name__)


class InvoiceExtractionError(RuntimeError):
    """Raised when the LLM cannot produce a valid invoice payload.

    Distinct from :class:`ValueError` so callers can tell "bad input"
    (unparseable PDF text upstream) from "bad extraction" (OpenAI
    returned malformed JSON, hit an auth error, etc.).
    """


_SYSTEM_PROMPT = """Jesteś asystentem specjalizującym się w ekstrakcji danych z faktur.
Otrzymasz tekst faktury (polski lub angielski). Zwróć dane w strukturze JSON zgodnej
z dostarczonym schematem.

Zasady:
- Kwoty zawsze jako liczby (float), bez separatorów tysięcznych, kropka jako separator dziesiętny.
- Daty w formacie ISO-8601 (YYYY-MM-DD). Jeśli data nieznana → null.
- NIP: tylko cyfry, bez myślników i prefiksu "PL". Jeśli brak NIP → null.
- line_items: jeśli pozycje nie są jawnie wyszczególnione w tekście, zwróć pustą listę.
- currency: trzyliterowy kod ISO (PLN, EUR, USD). Jeśli nie wskazano → "PLN".
- Nie zgaduj wartości których nie ma w tekście — użyj null dla pól opcjonalnych.
"""


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Return a cached OpenAI client.

    Raises:
        InvoiceExtractionError: If no API key is configured. Callers
            should check :func:`_should_use_mock` first rather than
            rely on catching this.
    """
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise InvoiceExtractionError(
                "OPENAI_API_KEY is not set; cannot instantiate OpenAI client"
            )
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _should_use_mock() -> bool:
    """Decide whether to bypass OpenAI and return a stub.

    Triggered by either an explicit strategy (``"mock"``) or the
    absence of an API key (protects CI where the secret is unset).
    """
    strategy = settings.EXTRACTOR_STRATEGY.lower()
    if strategy == "mock":
        return True
    return not settings.OPENAI_API_KEY


def _mock_extraction(text: str) -> ExtractedInvoice:  # noqa: ARG001
    """Return a deterministic stub invoice.

    The payload is intentionally unrealistic — it's a CI placeholder,
    not a silent success. The distinctive seller name
    ``"MOCK — extractor disabled"`` makes it obvious when the mock
    accidentally leaks into production output.
    """
    return ExtractedInvoice(
        invoice_number="MOCK/0001",
        issue_date=None,
        seller=Party(name="MOCK — extractor disabled", nip=None, address=None),
        buyer=Party(name="MOCK buyer", nip=None, address=None),
        line_items=[
            LineItem(
                description="Mock line item",
                quantity=Decimal("1"),
                unit_price=Decimal("0"),
                total=Decimal("0"),
            )
        ],
        totals=Totals(
            net=Decimal("0"),
            vat=Decimal("0"),
            gross=Decimal("0"),
            currency="PLN",
        ),
    )


@retry(
    retry=retry_if_exception_type((APIConnectionError, APITimeoutError, RateLimitError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _call_openai(text: str) -> LLMInvoiceResponse:
    """Invoke OpenAI Structured Outputs and parse the response.

    Retries only on transient failures (connection, timeout, rate
    limit). Auth errors, bad-request errors and malformed JSON all
    propagate after the first attempt.
    """
    client = _get_client()
    completion = client.beta.chat.completions.parse(
        model=settings.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Wyciągnij dane z poniższej faktury:\n\n{text}",
            },
        ],
        response_format=LLMInvoiceResponse,
        temperature=0,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise InvoiceExtractionError(
            "OpenAI returned no parsed payload — possibly refused or " "produced malformed JSON."
        )
    return parsed


def extract_invoice(text: str) -> ExtractedInvoice:
    """Extract structured invoice data from raw text.

    Args:
        text: The plain-text body of the invoice (as produced by
            :func:`app.services.pdf_text_extractor.extract_text`).

    Returns:
        A populated :class:`ExtractedInvoice`. In mock mode the stub
        payload is returned instead of calling OpenAI.

    Raises:
        ValueError: If ``text`` is empty or whitespace-only.
        InvoiceExtractionError: If OpenAI returns an error that
            survives the retry policy, or the response cannot be
            parsed into the schema.
    """
    if not text or not text.strip():
        raise ValueError("Cannot extract from empty text")

    if _should_use_mock():
        logger.info(
            "Invoice extractor in MOCK mode (strategy=%s, api_key_set=%s)",
            settings.EXTRACTOR_STRATEGY,
            bool(settings.OPENAI_API_KEY),
        )
        return _mock_extraction(text)

    try:
        wire_payload = _call_openai(text)
    except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
        raise InvoiceExtractionError(f"OpenAI transient error after retries: {exc!r}") from exc
    except InvoiceExtractionError:
        raise
    except Exception as exc:
        raise InvoiceExtractionError(f"OpenAI call failed: {exc!r}") from exc

    return from_llm_response(wire_payload)
