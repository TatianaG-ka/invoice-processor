# Invoice Processor API

> **KSeF-compatible invoice intelligence service:** PDF / KSeF XML → parse + extract → PostgreSQL → semantic search via Qdrant.

FastAPI microservice for Polish invoice processing with dual-schema KSeF support (FA(2) legacy + FA(3) current).

> **🚧 Status:** Active development. Not production-ready yet. See roadmap below.

---

## 🎯 Problem

Polish businesses receive 60–100 invoices monthly in mixed formats (PDF, KSeF XML, scans). Manual processing is slow, error-prone, and doesn't scale. Starting February 2026, KSeF FA(3) is mandatory for large Polish companies.

**Goal:** automate the full pipeline — ingest → parse → structured storage → semantic retrieval — with dual-schema KSeF compliance.

---

## 🏗️ Architecture (planned)

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   Client     │──────▶   FastAPI    │──────▶ Redis Queue  │
│  (API/n8n)   │      │   (async)    │      │    (RQ)      │
└──────────────┘      └──────────────┘      └──────┬───────┘
                             │                     │
                             │                     ▼
                      ┌──────▼──────┐      ┌──────────────┐
                      │ PostgreSQL  │◀─────│   Worker     │
                      │ (metadata)  │      │  - KSeF XML  │
                      └─────────────┘      │  - OCR       │
                             ▲             │  - LLM extract│
                             │             │  - Embedding │
                      ┌──────┴──────┐      └──────┬───────┘
                      │   Qdrant    │◀────────────┘
                      │ (semantic)  │
                      └─────────────┘
```

---

## 🛠️ Stack (planned)

| Layer | Technology | Why |
|---|---|---|
| API | **FastAPI** | Native async, Pydantic validation, auto OpenAPI |
| Relational DB | **PostgreSQL** | Invoice metadata, audit, relations |
| Vector DB | **Qdrant** | Semantic search across historical invoices |
| Queue | **Redis + RQ** | Async processing of long-running tasks |
| XML parsing | **lxml** | KSeF XSD-native, namespace-aware |
| OCR | **pytesseract + pdf2image** | Open-source, local |
| LLM | **OpenAI** (production) | Structured output JSON mode |
| Embeddings | **sentence-transformers (ONNX)** | Local, multilingual, optimized for production |
| Observability | **Langfuse** | LLM tracing + cost tracking |
| Tests | **pytest** | Unit + integration |
| Orchestration | **Docker Compose** | Multi-service dev |
| CI/CD | **GitHub Actions** | Lint + test on every push |

---

## 🚀 Quick start

```bash
# Clone
git clone https://github.com/TatianaG-ka/invoice-processor.git
cd invoice-processor

# Env vars
cp .env.example .env
# → edit .env with your keys

# Run
docker-compose up --build
```

Once running:
- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs

---

## 🧪 Tests

```bash
make test            # run all tests
make test-cov        # with coverage report
```

Status CI: ![CI](https://github.com/TatianaG-ka/invoice-processor/actions/workflows/ci.yml/badge.svg)

---

## 📐 Roadmap

### In progress
- [ ] KSeF FA(3) + FA(2) dual-schema XML parser with XSD validation
- [ ] PostgreSQL integration (async SQLAlchemy 2.0)
- [ ] Redis + RQ worker for async invoice processing
- [ ] Qdrant-based semantic search over historical invoices
- [ ] Langfuse observability for LLM calls
- [ ] Anomaly detection agent (compare new invoice to vendor history)
- [ ] n8n orchestration layer (Gmail → FastAPI → Slack approval)
- [ ] Docker ONNX optimization (~400MB image target)
- [ ] GCP Cloud Run deployment + benchmark (eval framework, N=30+)

### Currently shipped
- [x] FastAPI skeleton with `POST /invoices` endpoint
- [x] File validation (Content-Type, size limits)
- [x] Docker + docker-compose setup
- [x] GitHub Actions CI (ruff lint + format + pytest)

---

## 🔧 Makefile commands

```bash
make help         # list commands
make dev          # run locally (without Docker)
make up           # docker-compose up
make down         # docker-compose down
make logs         # tail logs
make test         # run tests
make lint         # ruff check
make format       # ruff format
make clean        # clean caches
```

---

## 👤 Author

**Tatiana Golińska** — Workflow Automation Engineer | Python + AI Integration

- 💼 [LinkedIn](https://www.linkedin.com/in/tatiana-golinska/)
- 📧 tatiana.golinska@gmail.com

---

## 📄 License

MIT
