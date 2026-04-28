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
echo "=== 5. Idempotency: re-POST the same XML, expect 200 + same id ==="
# Use a different fixture from step 2 so this test is independent —
# step 2 already claimed the FA(2) sample, so re-POSTing it would just
# return the step-2 invoice. Use FA(3) here for a clean first 201.
DEDUP_FIXTURE="docs/dane_testowe/ksef/faktura_fa3_sample.xml"
if [[ ! -f "${DEDUP_FIXTURE}" ]]; then
  echo "WARN: ${DEDUP_FIXTURE} missing; skipping idempotency check."
else
  FIRST_RESP="$(curl -sS -X POST -o /tmp/dedup-1.json -w "%{http_code}" \
    -F "file=@${DEDUP_FIXTURE};type=application/xml" \
    "${URL}/invoices/ksef")"
  FIRST_ID="$(python -c 'import json; print(json.load(open("/tmp/dedup-1.json"))["id"])')"
  echo "  First POST  → HTTP ${FIRST_RESP}, invoice_id=${FIRST_ID}"

  SECOND_RESP="$(curl -sS -X POST -o /tmp/dedup-2.json -w "%{http_code}" \
    -F "file=@${DEDUP_FIXTURE};type=application/xml" \
    "${URL}/invoices/ksef")"
  SECOND_ID="$(python -c 'import json; print(json.load(open("/tmp/dedup-2.json"))["id"])')"
  echo "  Second POST → HTTP ${SECOND_RESP}, invoice_id=${SECOND_ID}"

  if [[ "${SECOND_RESP}" == "200" && "${FIRST_ID}" == "${SECOND_ID}" ]]; then
    echo "  ✅ Idempotency working: same id, 201→200 status flip."
  else
    echo "  ❌ Idempotency NOT working — expected second POST to be 200 with id=${FIRST_ID}."
    echo "     Most likely cause: IDEMPOTENCY_REDIS_URL unset on Cloud Run."
    exit 1
  fi
fi

echo ""
echo "Smoke test OK. Check Langfuse dashboard for the generation trace."
