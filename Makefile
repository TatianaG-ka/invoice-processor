# ============================================
# Invoice Processor — Makefile
# ============================================
# Usage: make [target]
# List targets: make help

.PHONY: help install dev up down restart logs shell test test-cov lint format clean db-shell

help:  ## Show available commands
	@echo "Invoice Processor — available commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ===== Setup =====

install:  ## Install all dependencies (prod + dev)
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

# ===== Local development =====

dev:  ## Run the app locally (no Docker, with hot reload)
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# ===== Docker =====

up:  ## Start all services (docker-compose up)
	docker-compose up --build -d
	@echo ""
	@echo "  API:         http://localhost:8000"
	@echo "  Swagger UI:  http://localhost:8000/docs"
	@echo "  Postgres:    localhost:5432"
	@echo ""
	@echo "Logs: make logs"
	@echo "Stop: make down"

up-v2:  ## Start full stack (api + worker + postgres + redis + qdrant)
	docker-compose -f docker-compose.v2.yml up --build -d
	@echo ""
	@echo "  API:          http://localhost:8000"
	@echo "  Swagger UI:   http://localhost:8000/docs"
	@echo "  Qdrant UI:    http://localhost:6333/dashboard"
	@echo "  Postgres:     localhost:5432"
	@echo "  Redis:        localhost:6379"

down:  ## Stop all services
	docker-compose down
	-docker-compose -f docker-compose.v2.yml down

restart:  ## Restart services
	make down
	make up

logs:  ## Tail logs
	docker-compose logs -f

shell:  ## Shell inside the api container
	docker-compose exec api bash

db-shell:  ## PostgreSQL shell
	docker-compose exec postgres psql -U invoice_user -d invoices

# ===== Tests =====

test:  ## Run tests
	pytest -v

test-cov:  ## Tests with coverage report
	pytest --cov=app --cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

# ===== Code quality =====

lint:  ## Lint (ruff)
	ruff check app tests

format:  ## Format (black + ruff --fix)
	black app tests
	ruff check --fix app tests

typecheck:  ## Type checking (mypy)
	mypy app

# ===== Cleanup =====

clean:  ## Remove caches and temporary files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf uploads/ 2>/dev/null || true

clean-docker:  ## Remove containers, volumes and images (CAUTION — wipes Postgres data!)
	docker-compose down -v
	docker-compose -f docker-compose.v2.yml down -v
