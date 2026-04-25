#!/usr/bin/env bash
# ============================================================
# Deploy invoice-processor to Google Cloud Run from a local repo.
# ============================================================
# Reads secrets from a local .env (gitignored) and forwards them to
# Cloud Run via --set-env-vars. Nothing is logged to stdout beyond the
# variable *names* — values never echo to the terminal.
#
# Pre-reqs (run once per environment):
#   * gcloud auth login
#   * gcloud config set project <your-project-id>
#   * gcloud services enable run.googleapis.com \
#         cloudbuild.googleapis.com artifactregistry.googleapis.com
#
# Usage:
#   ./scripts/deploy_cloud_run.sh
#
# Region choice: europe-central2 (Warsaw) — closest to Neon's Frankfurt
# region, lowest latency for a Polish-recruiter demo.

set -euo pipefail

# ---------------------------------------------------------------------
# Load .env — no globbing, no word splitting on values.
# ---------------------------------------------------------------------
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found in the repo root."
  echo "Copy .env.example → .env and fill in production values."
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

# ---------------------------------------------------------------------
# Required variables — fail fast with a clear message instead of
# shipping a half-configured Cloud Run revision.
# ---------------------------------------------------------------------
: "${DATABASE_URL:?DATABASE_URL must be set in .env (Neon connection string)}"
: "${OPENAI_API_KEY:?OPENAI_API_KEY must be set in .env}"
: "${LANGFUSE_PUBLIC_KEY:?LANGFUSE_PUBLIC_KEY must be set in .env}"
: "${LANGFUSE_SECRET_KEY:?LANGFUSE_SECRET_KEY must be set in .env}"

# Optional-with-defaults.
: "${OPENAI_MODEL:=gpt-4o-mini}"
: "${LANGFUSE_HOST:=https://cloud.langfuse.com}"
: "${QDRANT_COLLECTION:=invoices}"

SERVICE_NAME="invoice-processor"
REGION="europe-central2"

echo "Deploying ${SERVICE_NAME} to Cloud Run in ${REGION}..."
echo "  DATABASE_URL: (redacted, $(echo -n "$DATABASE_URL" | wc -c) chars)"
echo "  OPENAI_MODEL: ${OPENAI_MODEL}"
echo "  LANGFUSE_HOST: ${LANGFUSE_HOST}"
echo "  QDRANT_URL: :memory: (embedded, reindex from Postgres on boot)"
echo ""

# ---------------------------------------------------------------------
# Deploy. --source . tells Cloud Build to pick up the repo Dockerfile,
# build the image, push it to Artifact Registry, and land it on Cloud
# Run in one step.
#
# --allow-unauthenticated: the demo URL is meant for recruiter clicks.
# --memory 2Gi: sentence-transformers pulls torch (~800 MB RSS with
#    the MiniLM model loaded), 1 GiB is too tight.
# --cpu 2: MiniLM encode is CPU-bound; one vCPU bottlenecks the first
#    reindex pass on any non-trivial DB.
# --min-instances 0: cold starts are acceptable for a demo — the
#    reindex loop keeps even a cold boot functional in seconds.
# --timeout 300: the first request after a cold start covers model
#    download + reindex. The default 60s would occasionally 504.
# ---------------------------------------------------------------------
gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8000 \
  --memory 2Gi \
  --cpu 2 \
  --min-instances 0 \
  --max-instances 3 \
  --timeout 300 \
  --set-env-vars "DATABASE_URL=${DATABASE_URL}" \
  --set-env-vars "OPENAI_API_KEY=${OPENAI_API_KEY}" \
  --set-env-vars "OPENAI_MODEL=${OPENAI_MODEL}" \
  --set-env-vars "LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY}" \
  --set-env-vars "LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY}" \
  --set-env-vars "LANGFUSE_HOST=${LANGFUSE_HOST}" \
  --set-env-vars "QDRANT_URL=:memory:" \
  --set-env-vars "QDRANT_COLLECTION=${QDRANT_COLLECTION}" \
  --set-env-vars "ENVIRONMENT=production" \
  --set-env-vars "LOG_LEVEL=INFO"

echo ""
echo "Deployed. Next: smoke-test with"
echo "  ./scripts/smoke_test_prod.sh <deployed-url>"
