# ============================================
# Invoice Processor - Docker image
# ============================================
# Multi-stage build dla mniejszego rozmiaru finalnego obrazu.
#
# WAŻNE: od dnia 5 projektu. Nie potrzebujesz tego w dniach 1-4.
#
# Build:   docker build -t invoice-processor .
# Run:     docker run -p 8000:8000 invoice-processor
# Shell:   docker run -it --rm invoice-processor bash

FROM python:3.11-slim AS builder

# System dependencies dla OCR i pdf2image
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Tesseract OCR + język polski
    tesseract-ocr \
    tesseract-ocr-pol \
    tesseract-ocr-eng \
    # pdf2image potrzebuje poppler
    poppler-utils \
    # build tools (dla psycopg2)
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Najpierw tylko requirements - caching warstwy Dockera
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Teraz kod aplikacji
COPY ./app ./app

# Expose port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=5.0)"

# Domyślne polecenie - uruchomienie FastAPI
# W docker-compose to nadpiszesz osobnym command dla workera
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
