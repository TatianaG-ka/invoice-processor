# Invoice Processor

> **KSeF-compatible invoice intelligence microservice.** Ingest a Polish invoice (PDF or KSeF XML), extract structured fields with an LLM, persist to Postgres, make the archive semantically searchable.

**Live demo:** https://invoice-processor-510066601703.europe-central2.run.app
([`/docs`](https://invoice-processor-510066601703.europe-central2.run.app/docs) for the interactive OpenAPI UI)

![CI](https://github.com/TatianaG-ka/invoice-processor/actions/workflows/ci.yml/badge.svg)

---

## What it does

Polish businesses receive 60–100 invoices per month across three shapes: scanned PDFs, text-layer PDFs, and — from **April 2026**, mandatory for virtually every B2B business in Poland (large taxpayers above PLN 200M revenue went live in February 2026) — KSeF XML. This service normalises all of them into one typed record and surfaces three things that matter downstream:

1. **Structured fields** behind `GET /invoices/{id}` — seller, buyer, line items, totals, dates — all in a consistent JSON shape regardless of ingestion path.
2. **Semantic retrieval** behind `GET /invoices/search?q=...` — cosine similarity over sentence embeddings of seller name + line-item descriptions, so "find invoices about printer toner" works without exact-string matching.
3. **LLM categorization** behind `POST /invoices/{id}/categorize` — RAG over Qdrant neighbours + `gpt-4o-mini` few-shot, persisted to the `invoices` row. Idempotent: subsequent calls return the cached category from Postgres in <100 ms with no LLM hit; `?force=true` overrides for prompt iterations.

---

## Architecture

```mermaid
flowchart LR
    Client([Client / n8n]) -->|POST /invoices PDF| API[FastAPI]
    Client -->|POST /invoices/ksef XML| API
    Client -->|GET /invoices/search| API
    Client -->|POST /invoices/{id}/categorize| API

    API -->|enqueue job| Queue[(Redis + RQ)]
    Queue --> Worker[RQ Worker]
    Worker -->|pdfplumber + pytesseract OCR fallback| Text[Raw text]
    Text -->|OpenAI Structured Outputs| Extract[ExtractedInvoice]

    API -->|KSeF XML: lxml dual-schema FA 2 / FA 3| Extract
    Extract -->|async SQLAlchemy| PG[(PostgreSQL / Neon)]
    Extract -->|sentence-transformers MiniLM 384-dim| QD[(Qdrant embedded)]

    API -->|RAG: top-3 neighbours| QD
    QD -->|few-shot examples| Cat[OpenAI categorization]
    Cat -->|persist category + confidence| PG

    API -->|@observe decorator| LF[Langfuse Cloud]

    PG -->|reindex on startup| QD

    subgraph Cloud Run
        API
        Worker
        QD
    end
```

**One-line summary of the read path:** embed query → Qdrant cosine top-K → hydrate full rows from Postgres (DB is system of record, vector store is refreshable).

---

## Stack

| Layer | Technology | Notes |
|---|---|---|
| HTTP API | FastAPI 0.115 + Pydantic v2 | Async endpoints, automatic OpenAPI |
| Relational DB | PostgreSQL 16 (Neon managed in prod) | async SQLAlchemy 2.0 + asyncpg |
| Vector store | Qdrant 1.11 (embedded in prod, server in dev) | 384-dim cosine over MiniLM |
| Background queue | Redis + RQ 2.0 | `POST /invoices` (PDF) enqueues, worker does extract + persist + index |
| PDF text | pdfplumber → pytesseract + pdf2image OCR fallback | Scanned PDFs handled automatically |
| KSeF XML | lxml with dual-schema support | FA(2) legacy + FA(3) `http://crd.gov.pl/wzor/2025/06/25/13775/` |
| LLM extraction | OpenAI `gpt-4o-mini` Structured Outputs | Deterministic JSON, ~$0.0003/call |
| LLM categorization (RAG) | OpenAI `gpt-4o-mini` + few-shot from Qdrant top-3 neighbours | Persisted in `invoices.category`; idempotent endpoint, ~$0.00016/call |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` | 384-dim, multilingual, ~80 MB |
| Observability | Langfuse Cloud | `@observe` on OpenAI calls (extraction + categorization), token/cost tracking |
| Testing | pytest + pytest-asyncio + fakeredis + in-memory Qdrant | 155 tests, ~5 s full run |
| CI | GitHub Actions (ruff + pytest against real Postgres + Redis services) | Green gate on every push |
| Deploy | Google Cloud Run (Warsaw, `europe-central2`) | Multi-stage Dockerfile, Cloud Build from source |

---

## API reference

| Route | Shape | Notes |
|---|---|---|
| `GET  /health` | `{status: "healthy"}` | Liveness probe |
| `POST /invoices` | 202 + `{job_id, status_url}` | PDF upload, enqueues to worker |
| `GET  /invoices/jobs/{id}` | `{status, invoice_id, error}` | Poll job status |
| `POST /invoices/ksef` | 201 + `StoredInvoice` | KSeF XML, synchronous (fast parse) |
| `GET  /invoices/{id}` | 200 + `StoredInvoice` | Retrieve by DB primary key |
| `GET  /invoices/search?q=...&limit=10` | 200 + `SearchResponse` | Semantic search, DB-hydrated results |
| `POST /invoices/{id}/categorize?force=false` | 201 (fresh) or 200 (cached) + `CategorizationResult` | RAG over Qdrant + LLM categorization; idempotent by default |

Every DB-touching endpoint narrows `sqlalchemy.exc.SQLAlchemyError` into a clean `503 Database temporarily unavailable.` — no stack trace ever reaches the wire.

---

## Live demo walkthrough

```bash
URL=https://invoice-processor-510066601703.europe-central2.run.app

# Health check
curl "$URL/health"
# {"status":"healthy"}

# Upload a KSeF FA(2) invoice (synthetic, no real NIPs in this repo)
curl -X POST -F "file=@docs/dane_testowe/ksef/faktura_fa2_sample.xml;type=application/xml" \
  "$URL/invoices/ksef"
# {"id":1,"invoice_number":"FV/FA2/001/2026","seller":{"name":"Acme Sp. z o.o.",...},"totals":{"gross":"1230.00",...}}

# Semantic search
curl "$URL/invoices/search?q=Acme"
# {"query":"Acme","results":[{"score":0.398,"invoice":{"id":1,...}}]}

# Re-POST the same invoice — 200 OK + same id (Redis idempotency, 24h TTL)
curl -X POST -F "file=@docs/dane_testowe/ksef/faktura_fa2_sample.xml;type=application/xml" \
  -w "\nHTTP %{http_code}\n" \
  "$URL/invoices/ksef"
# {"id":1,...}
# HTTP 200

# RAG-driven LLM categorization (first call: 201 + LLM hit, ~3 s)
curl -X POST "$URL/invoices/1/categorize"
# {"invoice_id":1,"category":"Konsulting i doradztwo","confidence":0.8,
#  "reasoning":"Faktura dotyczy usług konsultingowych...","cached":false}

# Same call again — 200 + cached, no LLM hit, <100 ms
curl -X POST "$URL/invoices/1/categorize" -w "\nHTTP %{http_code}\n"
# {"invoice_id":1,"category":"Konsulting i doradztwo","confidence":0.8,
#  "reasoning":null,"cached":true}
# HTTP 200
```

The `score` field is raw cosine similarity from MiniLM — the same sentence-transformers model that runs in production, not a test stub.

End-to-end verification (health → KSeF → retrieval → search → idempotency) is automated in [`scripts/smoke_test_prod.sh`](scripts/smoke_test_prod.sh) — see [`docs/idempotency_smoke_test.png`](docs/idempotency_smoke_test.png) for the live `201 → 200, same id` flip from a recent revision.

The full live API surface is browsable at [`/docs`](https://invoice-processor-510066601703.europe-central2.run.app/docs) (FastAPI's auto-generated Swagger UI from the same Pydantic schemas the code uses):

![Swagger UI on Cloud Run](docs/swagger_fastapi.png)

---

## Pipeline integration (n8n)

The service is consumed end-to-end by an n8n workflow that simulates a KSeF inbox poll. Two workflows are exported under [`n8n/`](n8n/):

| File | Purpose |
| --- | --- |
| [`n8n/01_ksef_ingestion.json`](n8n/01_ksef_ingestion.json) | Schedule (every 30 min) → generate 5 synthetic FA(3) invoices in a Code node → SplitInBatches → `POST /invoices/ksef` → branch on status code → Slack `#invoice-pipeline-demo` + Google Sheets `processed_invoices` audit row |
| [`n8n/99_error_handler.json`](n8n/99_error_handler.json) | Bound as `errorWorkflow` on the main flow. Error Trigger → extract context → fan-out to Sheets `errors` tab + Slack alert with execution id |

The HTTP node calls the live Cloud Run URL with `multipart/form-data`, `fullResponse: true` and `neverError: true` so the IF branch can route on `statusCode` instead of n8n auto-failing on 4xx/5xx. `typeValidation: "loose"` is set on the IF node because n8n's HTTP transport occasionally returns `statusCode` as a string — strict mode silently rejects `"201" === 201`.

To import: open n8n → workflows → Import from File → pick a JSON, then re-bind your own Slack and Google Sheets OAuth credentials (placeholder ids in the file are stripped). Sheet headers must match the schema id fields exactly (`timestamp, invoice_id, invoice_number, vendor_nip, amount_gross_pln, status` for success, `timestamp, workflow, execution_id, failed_node, error_message, payload_excerpt` for errors).

Note: n8n's `errorWorkflow` only triggers for production executions (active scheduled or webhook runs), not for manual "Execute Workflow" — to test the error path end-to-end, activate the main workflow and let it fire on its cron, or run the error workflow in isolation with a pinned Error Trigger sample payload.

---

## Observability

Every OpenAI call (extraction *and* categorization) is wrapped with `@observe(as_type="generation")` from the Langfuse SDK. Each trace carries the full prompt, the parsed structured output, the model name, token counts, latency, and cost.

| | |
|---|---|
| ![Traces list](docs/langfuse_traces_list.png) | Four observations (two SPAN parents, two GENERATION children) from two extraction calls. Model `gpt-4o-mini`, latency 5–6 s, **$0.000274 / call**. |
| ![Trace detail](docs/langfuse_trace_detail.png) | Extraction drilldown: raw FAKTURA VAT input on top, the parsed `ExtractedInvoice` JSON on the bottom — `invoice_number`, `seller.nip`, `buyer.nip`, `line_items`, `totals.net/vat/gross/currency`. |
| ![Categorization trace](docs/langfuse_categorize_trace_detail.png) | Categorization trace: full system prompt + few-shot user prompt (target invoice + Qdrant top-3 neighbours) + structured assistant response (`category`, `confidence`, `reasoning`). Latency 3–4 s, **$0.000156 / call** (cheaper than extraction — shorter prompt). |
| ![Dashboard](docs/langfuse_dashboard.png) | Dashboard view: 2 traces, $0.000548 cumulative cost, 2.24 K tokens, cost/time chart over last 24 h. |

The service degrades gracefully when Langfuse keys are absent: the decorator sees an empty `LANGFUSE_PUBLIC_KEY` and runs in no-op mode, which is how CI and local dev exercise the LLM paths without a Langfuse account.

---

## Architecture decisions

### ADR-001 — Async SQLAlchemy 2.0 + asyncpg
**Context:** FastAPI endpoints are async-native; a sync DB driver would either block the event loop or force thread-pool offload on every query.
**Decision:** `create_async_engine` + `AsyncSession` throughout. `_prepare_async_url()` auto-rewrites `postgresql://…?sslmode=require` into the asyncpg-friendly shape (prefix swap + `connect_args={"ssl": "require"}`) so the Neon connection string can be pasted verbatim from the dashboard.
**Consequence:** One concurrency model end-to-end. Sessions travel through FastAPI dependency injection in HTTP paths; the RQ worker owns its own sessionmaker for background jobs.

### ADR-002 — No Alembic
**Context:** Portfolio scope is one service, one schema, append-mostly workload. Alembic adds ceremony without payback.
**Decision:** `Base.metadata.create_all(checkfirst=True)` runs in the FastAPI lifespan. Safe on every container start — SQLAlchemy's default `checkfirst` will not recreate existing tables.
**Consequence:** No migration file to maintain, but also no schema-change safety net. Revisit if the row count grows past the demo's "hundreds" bound or if multiple instances need coordinated DDL.

### ADR-003 — Dual KSeF schema (FA(2) legacy + FA(3) current)
**Context:** The Polish Ministry of Finance rolled out FA(3) (`http://crd.gov.pl/wzor/2025/06/25/13775/`) as the mandatory format for large taxpayers (>PLN 200M revenue) from **February 2026**, with the universal B2B obligation following in **April 2026**. FA(2) documents will continue to exist in archives and email traffic for years.
**Decision:** `parse_ksef()` sniffs the root namespace and dispatches to one of two parsers. Both produce the same `ExtractedInvoice` domain model, so no downstream code knows which shape arrived.
**Consequence:** Ingestion accepts both shapes today; dropping FA(2) later is a one-function delete.

### ADR-004 — Embedded Qdrant + reindex-on-startup
**Context:** Cloud Run has ephemeral container storage — anything written to the filesystem disappears on instance replacement. An external vector store (Qdrant Cloud) would solve persistence but adds a moving part to a portfolio demo.
**Decision:** Ship Qdrant in-process (`QdrantClient(":memory:")`), and on every cold start walk every invoice in Postgres through `index_invoice` to rebuild the index. Postgres is the durable system of record; Qdrant is refreshable.
**Consequence:** Zero external search dependency; bounded by "hundreds of rows fit comfortably in memory." A production-scale variant would swap `:memory:` for `file://` on a mounted volume, or an external Qdrant — the wrapper already supports all three shapes via `_build_client()`.

### ADR-005 — Best-effort indexing, fail-loud persistence
**Context:** Two side stores get written on a successful ingest — Postgres (rows) and Qdrant (vectors). Coupling their availability would mean a vector-store blip causes lost invoices.
**Decision:** The repository `save` is on the critical path — a `SQLAlchemyError` propagates as `503`. `index_invoice` is wrapped in `try/except Exception → log + return False`: a broken embedder or a Qdrant outage degrades search coverage but never breaks the write path.
**Consequence:** The DB is the source of truth; the vector store is a secondary projection that can always be rebuilt. Matches the reindex-on-startup contract from ADR-004.

### ADR-006 — Redis idempotency on `POST /invoices/ksef`
**Context:** KSeF invoices arrive in bursts (n8n batches, retried HTTP timeouts). A retried POST of the same invoice should not parse and persist twice — that would double-count totals in downstream registers and double-fire Slack alerts.
**Decision:** Before parsing, hash the request to a `(seller_nip, invoice_number)` pair (Polish tax law guarantees this is unique per invoice forever) and `GET` the key from a managed Redis (Upstash in production, fakeredis under tests). Hit → return the originally-stored row with `200 OK` and the same `id`. Miss → save, then `SET key=invoice_id EX 86400` so the next 24h of retries are no-ops. A separate `IDEMPOTENCY_REDIS_URL` keeps this keyspace independent of the queue's Redis.
**Consequence:** `201 Created` (first time) and `200 OK` (cached) are both happy responses; consumers don't have to special-case status. Best-effort by design — a Redis outage logs a warning and falls through to a normal save (worse latency, possible duplicates during the outage window, but no failed requests). Tests cover both the cached-hit path and the Redis-outage fallthrough.

### ADR-007 — DB-cached LLM categorization (RAG + idempotency by default)
**Context:** Once an invoice is in the system, the next question is "what kind of expense is it?" — needed for ledger coding, expense-policy reporting, and downstream analytics. A pure-LLM endpoint would (a) cost a real-money OpenAI call on every request, even for invoices that haven't changed, and (b) miss the signal already in Qdrant: invoices similar to this one have *already* been categorized by a human or a prior LLM run.
**Decision:** `POST /invoices/{id}/categorize` runs a small RAG flow — embed target → Qdrant top-3 already-categorized neighbours → few-shot prompt → `gpt-4o-mini` Structured Outputs → persist `category` + `category_confidence` to the `invoices` row. Subsequent calls return the persisted value with `cached=true` and `200 OK` (no LLM hit), mirroring the idempotency contract from ADR-006. `?force=true` overrides the cache for prompt-engineering iterations. Schema migration (two new columns + index) is applied via [`scripts/migrate_add_category.sql`](scripts/migrate_add_category.sql) — idempotent `IF NOT EXISTS` — because ADR-002 explicitly defers Alembic; for the demo's "few-tables, append-mostly" shape, raw SQL scripts under `scripts/` are the canonical migration channel.
**Consequence:** Re-categorization is free (DB read), and prompt changes can be A/B-tested via `?force=true` without touching the schema. The persisted `reasoning` is intentionally not stored — only `category` + `confidence` — to keep the row narrow; the reasoning lives in the response of the originating call and (with full prompt + output) in the Langfuse trace. Operational caveat: the categorize path needs Qdrant ready, so the first call after a cold start can hit the reindex window (see Known limitations below).

### ADR-008 — n8n as a loose-coupled pipeline client
**Context:** A real KSeF inbox simulation needs an orchestration layer that polls, fans out, retries, and alerts on errors. Building that into the FastAPI service would conflate API surface with workflow logic; a separate n8n instance keeps each side focused.
**Decision:** Two exported workflows under [`n8n/`](n8n/) live alongside the service in the repo. The HTTP node uses `fullResponse: true` + `neverError: true` + `IF` `typeValidation: "loose"` so the API can return raw `201`/`200`/`4xx`/`5xx` without n8n auto-failing — the IF branch routes on `statusCode` instead. The `errorWorkflow` binding fans non-2xx outcomes to a `errors` audit sheet + Slack alert, and the main flow logs `processed_invoices` rows for successes. The API itself stays unaware of n8n: it returns standard HTTP and lets the caller decide retry / alert policy.
**Consequence:** Either side can evolve without breaking the other — the API can change response shapes (additive), n8n can change credentials, schedules, or fan-outs without touching code. The trade-offs (statusCode-as-string from n8n's HTTP transport, errorWorkflow only firing on production runs not manual executions) are documented in [Pipeline integration (n8n)](#pipeline-integration-n8n) above so a recruiter or maintainer can re-import without trial-and-error.

---

## Known limitations

These are deliberate trade-offs documented up-front. None block the demo; each has a documented escape hatch when scope grows.

| Limitation | Impact | Why it's acceptable today | Path to fix |
|---|---|---|---|
| **Cold-start window on Cloud Run** (ADR-004 + 005) | First request after `--min-instances=0` idle can hit a 503 while Qdrant reindex from Postgres + MiniLM model load is in flight. Reproducible: a lone `?force=true` after 15 min idle returns 503; retrying 10 s later returns 201. | A demo deployment doesn't justify always-warm pricing. Cache-hit path (`/categorize` w/o `force`) is DB-only and survives even before Qdrant is ready. | `--min-instances=1` (paid), or migrate to external Qdrant Cloud (removes the reindex window entirely). |
| **PDF endpoint isn't deployed in Cloud Run** | `POST /invoices` (PDF) requires the RQ worker + a worker-side Redis; the live demo runs only the API container, so PDF upload returns a job_id whose status will never advance. KSeF + categorize + search are demo-facing. | The PDF path is fully exercised by the test suite and `docker-compose.v2.yml`; deploying the worker would double infra cost for one extra ingestion path. | Spin a second Cloud Run service for the worker (same image, override the entrypoint to `rq worker default`) once volume warrants it. |
| **Langfuse Hobby tier — 30-day retention + variable flush latency** | Public dashboard URLs expire and traces older than 30 days are deleted. Flush latency from Cloud Run to Langfuse Cloud usually takes <60 s but has been observed up to ~20 min under load. | Static screenshots in [`docs/langfuse_*.png`](docs/) are checked in as permanent evidence. The service still works without Langfuse — keys absent → no-op decorator. | Upgrade to a paid Langfuse plan or self-host (the `@observe` instrumentation is portable). |

---

## Local development

```bash
git clone https://github.com/TatianaG-ka/invoice-processor.git
cd invoice-processor
cp .env.example .env          # fill in OPENAI_API_KEY; leave LANGFUSE_* blank for offline dev

docker-compose -f docker-compose.v2.yml up --build
# API:       http://localhost:8000
# Swagger:   http://localhost:8000/docs
# Postgres:  localhost:5432
# Redis:     localhost:6379
# Qdrant:    http://localhost:6333
```

The worker and API share one image; docker-compose overrides the default `uvicorn` command with `rq worker default` for the worker service.

---

## Tests

```bash
pytest                         # full suite, ~5 seconds
pytest --cov=app --cov-report=term-missing
```

**155 tests** cover every module that moves data — PDF text + OCR, OpenAI extractor, KSeF parser (FA(2) + FA(3) fixtures), repository + persistence, queue tasks, vector store + reindex, search endpoint, DB-URL normalisation, HTTP error boundaries, **Redis idempotency layer** (retry-dedup + Redis-outage fallthrough), **LLM categorization** (happy path + cache hit + `?force=true` + zero-shot fallback when Qdrant empty + 502 on LLM failure). Hermetic by design: in-memory SQLite via aiosqlite, `fakeredis` + synchronous RQ (sync API for the queue, async API for idempotency), `QdrantClient(":memory:")`, deterministic fake embedder, mocked OpenAI in categorize tests — no external network on any test run.

---

## Project layout

```
app/
  main.py                   # FastAPI app + routes + lifespan (create_all + reindex_all)
  config.py                 # pydantic-settings + load_dotenv for 3rd-party SDKs
  db/
    base.py                 # async engine + session factory + Neon URL normalisation
    models.py               # Invoice ORM row
    repositories/
      invoice_repository.py # ORM ↔ domain model boundary
    session.py              # FastAPI dependency
  queue/
    connection.py           # lazy Redis + RQ queue singletons
    tasks.py                # process_pdf_invoice (PDF → text → LLM → DB → index)
  schemas/
    invoice.py              # ExtractedInvoice, StoredInvoice, SearchHit, SearchResponse
    job.py                  # JobAccepted, JobStatus
    category.py             # InvoiceCategory enum + LLMCategorizationResponse + CategorizationResult
  services/
    pdf_text_extractor.py   # pdfplumber + OCR fallback
    invoice_extractor.py    # OpenAI Structured Outputs, Langfuse-instrumented
    invoice_categorizer.py  # RAG over Qdrant + OpenAI categorization, idempotent persist
    ksef_parser.py          # dual-schema FA(2) + FA(3) XML → ExtractedInvoice
    embedder.py             # SentenceTransformer lazy singleton
    vector_store.py         # Qdrant wrapper + index_invoice + reindex_all

scripts/
  deploy_cloud_run.sh       # env-loading wrapper around `gcloud run deploy --source .`
  smoke_test_prod.sh        # curl-driven end-to-end verification of a live revision
  migrate_add_category.sql  # idempotent `ALTER TABLE` for ADR-007 columns (Neon SQL editor)

docs/
  dane_testowe/             # synthetic PDF + KSeF fixtures (no real NIPs)
  langfuse_*.png            # observability screenshots (Hobby tier has 30-day retention)

tests/                      # 155 tests, hermetic, ~5 s
```

---

## Author

**Tatiana Golińska** — Workflow Automation Engineer (n8n, Python, AI integration)
[LinkedIn](https://www.linkedin.com/in/tatiana-golinska/)

---

## License

MIT
