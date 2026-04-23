#!/usr/bin/env bash
# ============================================================
# Smoke-test a deployed Cloud Run revision.
# ============================================================
# Runs the minimum set of requests that prove the service is actually
# wired end-to-end: health check, KSeF upload (the sync write path that
# also exercises the DB + embedder + Qdrant), retrieval by id, and a
# semantic search query against the just-saved invoice.
#
# Usage:
#   ./scripts/smoke_test_prod.sh https://invoice-processor-xxx-ew.a.run.app

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <cloud-run-url>"
  echo "Example: $0 https://invoice-processor-abc123-ew.a.run.app"
  exit 1
fi

URL="$1"
# Strip trailing slash so the concatenations below are predictable.
URL="${URL%/}"

# Synthetic KSeF XML that ships in the repo — no real NIPs or PII.
FIXTURE="docs/dane_testowe/ksef/faktura_fa2_sample.xml"
if [[ ! -f "${FIXTURE}" ]]; then
  echo "ERROR: ${FIXTURE} missing; cannot run the KSeF step."
  exit 1
fi

echo "=== 1. GET /health ==="
curl -sSf -o /dev/null -w "HTTP %{http_code}\n" "${URL}/health"

echo ""
echo "=== 2. POST /invoices/ksef (sync write + embed + upsert) ==="
INVOICE_JSON="$(curl -sSf -X POST \
  -F "file=@${FIXTURE};type=application/xml" \
  "${URL}/invoices/ksef")"
echo "${INVOICE_JSON}" | head -c 400
echo ""

INVOICE_ID="$(echo "${INVOICE_JSON}" | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
echo "  Stored as invoice_id=${INVOICE_ID}"

echo ""
echo "=== 3. GET /invoices/${INVOICE_ID} ==="
curl -sSf "${URL}/invoices/${INVOICE_ID}" | head -c 400
echo ""

echo ""
echo "=== 4. GET /invoices/search?q=Acme ==="
curl -sSf "${URL}/invoices/search?q=Acme" | head -c 400
echo ""

echo ""
echo "Smoke test OK. Check Langfuse dashboard for the generation trace."
