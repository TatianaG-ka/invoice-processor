"""Sentence embedding for semantic search.

Wraps a :class:`sentence_transformers.SentenceTransformer` model behind a
lazy singleton. The model itself (``all-MiniLM-L6-v2``) is ~80 MB and
multi-second to load, so:

* The first :func:`embed` call downloads/loads it and caches on disk
  (HuggingFace cache).
* Subsequent calls are fast (~milliseconds per encode).
* Tests never call the real model — they monkeypatch :func:`embed` with
  a deterministic fake so CI stays hermetic and fast.

Output dimensionality is **384** (``all-MiniLM-L6-v2`` fixed) — the
Qdrant collection is created with the same size in
:mod:`app.services.vector_store`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

EMBEDDING_DIM = 384
"""Dimensionality of ``all-MiniLM-L6-v2`` output vectors. Hard-coded here
(not read from the loaded model) so the Qdrant collection can be created
before the model itself is instantiated, and so swapping models later is
an explicit change in one place."""

MODEL_NAME = "all-MiniLM-L6-v2"


if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """Return the lazily-loaded SentenceTransformer model.

    Import is deferred until first call so the ~80 MB model and its torch
    dependency are not loaded at application startup when only the KSeF
    or raw-text paths are in use.
    """
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(text: str) -> list[float]:
    """Return a 384-dim embedding for ``text`` as a plain Python list.

    A plain list (not a NumPy array) is what the Qdrant client expects
    for upserts, and it serialises cleanly for any future caller that
    wants to store vectors alongside rows.
    """
    vector = _get_model().encode(text, convert_to_numpy=True, normalize_embeddings=False)
    return vector.tolist()


def reset() -> None:
    """Drop the cached model. Used by tests that swap the real model
    for a deterministic fake at import time."""
    global _model
    _model = None
