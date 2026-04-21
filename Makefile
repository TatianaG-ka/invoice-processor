# ============================================
# Invoice Processor - Makefile
# ============================================
# Użycie: make [cel]
# Lista celów: make help

.PHONY: help install dev up down restart logs shell test test-cov lint format clean db-shell

help:  ## Pokaż dostępne komendy
	@echo "Invoice Processor - dostępne komendy:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ===== Setup =====

install:  ## Instaluj wszystkie zależności (prod + dev)
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

# ===== Local development =====

dev:  ## Uruchom aplikację lokalnie (bez Dockera, z hot reload)
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# ===== Docker =====

up:  ## Uruchom wszystkie serwisy (docker-compose up)
	docker-compose up --build -d
	@echo ""
	@echo "  API:         http://localhost:8000"
	@echo "  Swagger UI:  http://localhost:8000/docs"
	@echo "  Postgres:    localhost:5432"
	@echo ""
	@echo "Logi: make logs"
	@echo "Stop: make down"

up-v2:  ## Uruchom pełną wersję PRO (api + worker + postgres + redis + qdrant)
	docker-compose -f docker-compose.v2.yml up --build -d
	@echo ""
	@echo "  API:          http://localhost:8000"
	@echo "  Swagger UI:   http://localhost:8000/docs"
	@echo "  Qdrant UI:    http://localhost:6333/dashboard"
	@echo "  Postgres:     localhost:5432"
	@echo "  Redis:        localhost:6379"

down:  ## Zatrzymaj wszystkie serwisy
	docker-compose down
	-docker-compose -f docker-compose.v2.yml down

restart:  ## Restart serwisów
	make down
	make up

logs:  ## Pokaż logi (follow)
	docker-compose logs -f

shell:  ## Shell wewnątrz kontenera api
	docker-compose exec api bash

db-shell:  ## Shell PostgreSQL
	docker-compose exec postgres psql -U invoice_user -d invoices

# ===== Testy =====

test:  ## Uruchom testy
	pytest -v

test-cov:  ## Testy z pokryciem kodu
	pytest --cov=app --cov-report=term-missing --cov-report=html
	@echo "Raport HTML: htmlcov/index.html"

# ===== Jakość kodu =====

lint:  ## Sprawdź kod (ruff)
	ruff check app tests

format:  ## Sformatuj kod (black + ruff --fix)
	black app tests
	ruff check --fix app tests

typecheck:  ## Type checking (mypy)
	mypy app

# ===== Porządki =====

clean:  ## Usuń cache i pliki tymczasowe
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf uploads/ 2>/dev/null || true

clean-docker:  ## Usuń kontenery, wolumeny i obrazy (OSTROŻNIE - kasuje dane w postgres!)
	docker-compose down -v
	docker-compose -f docker-compose.v2.yml down -v
