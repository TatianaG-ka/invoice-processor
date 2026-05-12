# syntax=docker/dockerfile:1.7
# ============================================
# Invoice Processor — multi-stage Docker image
# ============================================
# Two stages:
#   * builder: installs build toolchain + compiles every pip dep into a
#     dedicated virtualenv at /opt/venv.
#   * runtime: fresh slim base, only runtime system deps (tesseract,
#     poppler), venv copied over from builder, app code added last.
#
# Why the split: torch + sentence-transformers + asyncpg pull in a
# compiler and C headers we must not ship to production. The runtime
# stage is free of gcc/build-essential/libpq-dev, which keeps the final
# image substantially smaller and cuts attack surface.
#
# Dependency source: pyproject.toml ([project.dependencies]) — single
# source of truth for runtime pins. Layer-caching trick below installs
# deps against an empty `app/` so the deps layer is invalidated only
# when pyproject.toml changes, not when source under app/ changes.
#
# Build:   docker build -t invoice-processor .
# Run:     docker run -p 8000:8000 invoice-processor
# Shell:   docker run -it --rm invoice-processor bash

# ---------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build-only system packages. These never reach the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Isolated virtualenv so the runtime stage can copy exactly what it
# needs — no interference with the system Python.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# --- Layer 1: deps only (cached unless pyproject.toml changes) -------
# README.md is required because pyproject.toml references it via
# `readme = "README.md"`. Without it, pip install errors out.
# Empty app/__init__.py is the trick: setuptools sees a package to
# install, deps resolve against pyproject, but no source is yet baked
# in. The deps layer caches independently of app/ changes — saves
# ~750 MB of torch redownload on every code edit.
COPY pyproject.toml README.md ./
RUN mkdir app && touch app/__init__.py \
    && pip install --upgrade pip \
    && pip install --no-cache-dir . \
    && rm -rf app

# --- Layer 2: real source (cached unless app/ changes) ---------------
# Re-install with --no-deps so deps already in the venv stay untouched
# and only the package metadata is refreshed to point at the real
# source tree.
COPY ./app ./app
RUN pip install --no-deps --no-cache-dir .

# ---------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Runtime-only system packages. Tesseract + poppler are needed by the
# OCR fallback inside the worker (pdf2image → pytesseract). API-only
# containers technically do not need them, but we keep one image for
# both the api and worker service so the same artefact ships
# everywhere.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-pol \
        tesseract-ocr-eng \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy the prebuilt virtualenv from the builder stage — no pip install
# happens in this layer.
COPY --from=builder /opt/venv /opt/venv

WORKDIR /code
COPY ./app ./app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=5.0)"

# Default command = API. docker-compose overrides this with
# ``rq worker ... default`` for the worker service.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
