"""LLM-driven categorization of persisted invoices.

Wires the existing pieces into a small RAG flow:

1. Fetch the target invoice from Postgres.
2. Embed the target's seller + line-item text and ask Qdrant for the
   top-3 most-similar already-categorized invoices.
3. Build a few-shot prompt from those neighbours and ask
   ``gpt-4o-mini`` (Structured Outputs) for the matching
   :class:`InvoiceCategory`, a confidence score, and a one-sentence
   reasoning.
4. Persist the result back to the invoice row.

The flow is **idempotent by default**: once an invoice has a
``category`` column populated, subsequent calls return the cached
value with ``cached=True`` and skip the LLM call. ``force=True``
overrides the cache and re-categorizes (useful for prompt-engineering
iterations and the live demo path).

Wrapped with ``@observe(as_type="generation")`` so every LLM call
ships to Langfuse with model + token + cost metadata, identical to
the extraction pipeline (ADR-006). Disabled keys → no-op decorator.
"""

from __future__ import annotations

import asyncio
import logging

from langfuse.decorators import langfuse_context, observe
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.db.models import Invoice
from app.db.repositories.invoice_repository import (
    InvoiceRepository,
    orm_to_stored_invoice,
)
from app.schemas.category import (
    CategorizationResult,
    InvoiceCategory,
    LLMCategorizationResponse,
)
from app.services import embedder
from app.services.vector_store import VectorStore, build_invoice_text

logger = logging.getLogger(__name__)


def _langfuse_enabled() -> bool:
    """Match the gating logic from :mod:`app.services.invoice_extractor`."""
    return bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)


class InvoiceNotFoundError(LookupError):
    """The target invoice id does not exist in the DB."""


class InvoiceCategorizationError(RuntimeError):
    """Raised when the LLM cannot produce a valid categorization payload."""


_CATEGORY_LIST = "\n".join(f"- {c.value}" for c in InvoiceCategory)


_SYSTEM_PROMPT = f"""Jesteś asystentem księgowym. Twoim zadaniem jest przypisanie
faktury do JEDNEJ z poniższych kategorii:

{_CATEGORY_LIST}

Zasady:
- Wybierz dokładnie jedną kategorię z listy. Jeśli żadna nie pasuje wyraźnie, użyj "Inne".
- Bazuj głównie na opisach pozycji (line items) i nazwie sprzedawcy.
- Confidence to liczba 0.0–1.0 odzwierciedlająca pewność dopasowania.
  - 0.9+ gdy opis jest jednoznaczny ("hosting serwerów", "abonament telefoniczny").
  - 0.6–0.8 gdy są wskazówki ale jest dwuznaczność (np. "konsulting IT" — Konsulting czy IT?).
  - <0.5 gdy musisz zgadywać — wtedy wybierz "Inne" z confidence 0.5.
- Reasoning to JEDNO zdanie po polsku wyjaśniające wybór.
- Otrzymasz kilka przykładów już skategoryzowanych faktur — traktuj je jako wzorzec stylu,
  nie jako bezwzględną regułę. Twoja faktura może wymagać innej kategorii.
"""


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Return a cached OpenAI client (mirrors :mod:`invoice_extractor`)."""
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise InvoiceCategorizationError(
                "OPENAI_API_KEY is not set; cannot instantiate OpenAI client"
            )
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _format_invoice_for_prompt(invoice: Invoice) -> str:
    """Render an invoice's salient text fields for the LLM prompt.

    Mirrors the embedding text used for retrieval (seller + line-item
    descriptions) so the prompt and the retrieval are pointed at the
    same signal.
    """
    descriptions = "; ".join(item.get("description", "") for item in (invoice.line_items or []))
    return (
        f"Sprzedawca: {invoice.seller_name}\n"
        f"Numer faktury: {invoice.invoice_number or '(brak)'}\n"
        f"Pozycje: {descriptions or '(brak pozycji)'}\n"
        f"Kwota brutto: {invoice.total_gross} {invoice.currency}"
    )


def _build_user_prompt(target: Invoice, examples: list[Invoice]) -> str:
    """Assemble a few-shot user message: examples + target."""
    parts: list[str] = []
    for i, ex in enumerate(examples, start=1):
        parts.append(
            f"### Przykład {i} (kategoria: {ex.category})\n" f"{_format_invoice_for_prompt(ex)}"
        )
    parts.append(
        "### Faktura do skategoryzowania\n"
        f"{_format_invoice_for_prompt(target)}\n\n"
        "Zwróć kategorię, confidence i jednozdaniowe uzasadnienie."
    )
    return "\n\n".join(parts)


@observe(as_type="generation", name="openai-invoice-categorization")
@retry(
    retry=retry_if_exception_type((APIConnectionError, APITimeoutError, RateLimitError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _call_openai(
    target: Invoice,
    examples: list[Invoice],
) -> LLMCategorizationResponse:
    """Issue the Structured Outputs call and return the parsed payload.

    ``@observe`` ships a Langfuse generation trace with the prompt, the
    parsed response, and (post-call) the token + cost metadata. The
    decorator is a no-op when keys are blank, so unit tests without
    Langfuse credentials run identically.
    """
    client = _get_client()
    user_prompt = _build_user_prompt(target, examples)
    completion = client.beta.chat.completions.parse(
        model=settings.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format=LLMCategorizationResponse,
        temperature=0,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise InvoiceCategorizationError("OpenAI returned no parsed payload for categorization.")

    if _langfuse_enabled():
        try:
            langfuse_context.update_current_observation(
                input=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                output=parsed.model_dump(),
                model=settings.OPENAI_MODEL,
                usage={
                    "promptTokens": completion.usage.prompt_tokens,
                    "completionTokens": completion.usage.completion_tokens,
                    "totalTokens": completion.usage.total_tokens,
                },
            )
        except Exception:  # noqa: BLE001 — observability must not break categorization
            logger.exception("Langfuse observation update failed (non-fatal)")

    return parsed


async def _retrieve_similar_examples(
    *,
    target: Invoice,
    store: VectorStore,
    session: AsyncSession,
    limit: int = 3,
) -> list[Invoice]:
    """Find up to ``limit`` already-categorized neighbours from Qdrant.

    Best-effort: a Qdrant outage or an empty index just means we ask
    the LLM zero-shot. Skips the target itself and any neighbour that
    has not yet been categorized — the LLM benefits from labelled
    examples, not from "TBD" bookmarks.
    """
    target_text = build_invoice_text(orm_to_stored_invoice(target))
    if not target_text:
        return []
    try:
        query_vector = await asyncio.to_thread(embedder.embed, target_text)
        hits = await asyncio.to_thread(store.search, query_vector, limit + 1)
    except Exception:  # noqa: BLE001 — RAG retrieval is best-effort, not critical path
        logger.exception(
            "Qdrant retrieval failed for invoice id=%d; falling back to zero-shot.",
            target.id,
        )
        return []

    repo = InvoiceRepository(session)
    examples: list[Invoice] = []
    for invoice_id, _score in hits:
        if invoice_id == target.id:
            continue
        row = await repo.get_by_id(invoice_id)
        if row is None or not row.category:
            continue
        examples.append(row)
        if len(examples) >= limit:
            break
    return examples


async def categorize_invoice(
    invoice_id: int,
    *,
    session: AsyncSession,
    store: VectorStore,
    force: bool = False,
) -> tuple[CategorizationResult, bool]:
    """Categorize an invoice; return (result, was_fresh_call).

    Returns a tuple so the caller (the FastAPI route) can map a fresh
    call to ``201 Created`` and a cache hit to ``200 OK`` — the same
    flip pattern as ADR-006 idempotency on ``POST /invoices/ksef``.

    Raises:
        InvoiceNotFoundError: ``invoice_id`` is not in the DB.
        InvoiceCategorizationError: OpenAI failure that survives the
            retry policy.
    """
    repo = InvoiceRepository(session)
    target = await repo.get_by_id(invoice_id)
    if target is None:
        raise InvoiceNotFoundError(f"Invoice id={invoice_id} not found.")

    # Cached path — return previously persisted categorization.
    if target.category and not force:
        return (
            CategorizationResult(
                invoice_id=target.id,
                category=InvoiceCategory(target.category),
                confidence=float(target.category_confidence or 0.0),
                reasoning=None,
                cached=True,
            ),
            False,
        )

    examples = await _retrieve_similar_examples(target=target, store=store, session=session)

    try:
        llm_response = await asyncio.to_thread(_call_openai, target, examples)
    except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
        raise InvoiceCategorizationError(f"OpenAI transient error after retries: {exc!r}") from exc
    except InvoiceCategorizationError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface OpenAI errors as our domain error
        raise InvoiceCategorizationError(f"OpenAI call failed: {exc!r}") from exc

    updated = await repo.update_category(
        invoice_id=target.id,
        category=llm_response.category.value,
        confidence=llm_response.confidence,
    )
    if updated is None:
        # Race condition: invoice deleted between fetch + update.
        raise InvoiceNotFoundError(f"Invoice id={invoice_id} disappeared during categorization.")

    return (
        CategorizationResult(
            invoice_id=updated.id,
            category=llm_response.category,
            confidence=llm_response.confidence,
            reasoning=llm_response.reasoning,
            cached=False,
        ),
        True,
    )
