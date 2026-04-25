"""LLM-driven extraction of structured data from invoice text.

The service accepts the raw text yielded by
:func:`app.services.pdf_text_extractor.extract_text` and returns a
strongly-typed :class:`~app.schemas.invoice.ExtractedInvoice` by
calling OpenAI with Structured Outputs (strict JSON schema).

Retry policy: transient OpenAI errors are retried up to three times
with exponential backoff (``tenacity``). Any non-transient failure is
surfaced as :class:`InvoiceExtractionError`.

Tests that need a deterministic invoice payload without hitting
OpenAI patch :func:`extract_invoice` directly via the
``force_mock_extractor`` conftest fixture; there is no production
"mock mode" toggle here.
"""

from __future__ import annotations

import logging

from langfuse.decorators import langfuse_context, observe
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.schemas.invoice import (
    ExtractedInvoice,
    LLMInvoiceResponse,
    from_llm_response,
)

logger = logging.getLogger(__name__)


def _langfuse_enabled() -> bool:
    """True only when the Langfuse SDK has credentials to talk home.

    Used as a gate around ``langfuse_context.update_current_observation``
    — that call validates its arguments even when the SDK is disabled,
    so calling it in CI (where the keys are blank) raises spurious
    ``ValueError`` onto stderr. Cheapest fix: skip it when we know
    tracing is off.
    """
    return bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)


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
- Kwoty zawsze jako stringi (np. "123.45"), bez separatorów tysięcznych, kropka jako separator dziesiętny.
  Stringi zachowują pełną precyzję — nie konwertuj na liczby.
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
        InvoiceExtractionError: If no API key is configured. The
            extractor has no fallback behaviour — callers either set
            ``OPENAI_API_KEY`` or patch :func:`extract_invoice` in
            tests.
    """
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise InvoiceExtractionError(
                "OPENAI_API_KEY is not set; cannot instantiate OpenAI client"
            )
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


@observe(as_type="generation", name="openai-invoice-extraction")
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

    Wrapped with Langfuse ``@observe(as_type="generation")`` so every
    live call ships to the configured project as a generation trace.
    The decorator is a no-op when ``LANGFUSE_PUBLIC_KEY`` is blank
    (CI, local dev), so turning observability off costs nothing.
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
            "OpenAI returned no parsed payload — possibly refused or produced malformed JSON."
        )

    # Enrich the Langfuse trace with model + token usage. Only do this
    # when tracing is actually enabled: ``update_current_observation``
    # validates its arguments even on a disabled SDK, and we don't
    # want stderr noise in CI. A belt-and-braces try/except keeps any
    # future Langfuse API quirk from taking down extraction.
    if _langfuse_enabled():
        try:
            langfuse_context.update_current_observation(
                model=settings.OPENAI_MODEL,
                usage={
                    "promptTokens": completion.usage.prompt_tokens,
                    "completionTokens": completion.usage.completion_tokens,
                    "totalTokens": completion.usage.total_tokens,
                },
            )
        except Exception:  # noqa: BLE001 — observability must not break extraction
            logger.exception("Langfuse observation update failed (non-fatal)")

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

    try:
        wire_payload = _call_openai(text)
    except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
        raise InvoiceExtractionError(f"OpenAI transient error after retries: {exc!r}") from exc
    except InvoiceExtractionError:
        raise
    except Exception as exc:
        raise InvoiceExtractionError(f"OpenAI call failed: {exc!r}") from exc

    return from_llm_response(wire_payload)
