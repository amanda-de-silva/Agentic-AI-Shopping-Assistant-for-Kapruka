.PHONY: help install install-backend install-frontend setup \
        dev dev-backend dev-frontend build build-frontend \
        migrate seed smoke-mcp test lint fmt freeze compile \
        docker-up docker-down docker-logs clean check-env

# Load root .env when present. Backend settings also read this file directly.
-include .env

BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 5173

# Windows-friendly: invoke Python and tools through python.exe rather than
# uvicorn.exe / pytest.exe wrapper scripts.
PYTHON := .venv/Scripts/python.exe

help:
	@echo.
	@echo Kapruka AI Concierge Make targets:
	@echo   make setup             Create venv and install backend + frontend
	@echo   make install           Install all dependencies
	@echo   make check-env         Check that the root .env exists
	@echo   make migrate           Apply Supabase/Postgres migrations
	@echo   make dev-backend       Start FastAPI backend
	@echo   make dev-frontend      Start Vite frontend
	@echo   make dev               Start backend and frontend together (GNU Make -j)
	@echo   make build             Build frontend and compile Python source
	@echo   make test              Run backend tests
	@echo   make lint              Run Ruff checks
	@echo   make fmt               Format backend source
	@echo   make smoke-mcp         Run MCP smoke test
	@echo   make docker-up         Start Docker Compose services
	@echo   make docker-down       Stop Docker Compose services
	@echo   make docker-logs       Follow Docker Compose logs
	@echo   make clean             Remove venv and frontend node_modules

setup: install

install: install-backend install-frontend

install-backend:
	python -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

install-frontend:
	cd frontend && npm install

check-env:
	@if not exist .env (echo ERROR: .env was not found. Create it in the project root. && exit /b 1)
	@echo .env found.

# Development servers -------------------------------------------------------

dev-backend:
	$(PYTHON) -m uvicorn backend.main:app --reload --host 0.0.0.0 --port $(BACKEND_PORT)

dev-frontend:
	cd frontend && npm run dev -- --host 0.0.0.0 --port $(FRONTEND_PORT)

# Run both servers. GNU Make launches prerequisites in parallel when -j is used.
# The recursive invocation forces parallel mode so `make dev` works as one command.
dev:
	$(MAKE) -j2 dev-backend dev-frontend

# Verification/build --------------------------------------------------------

compile:
	$(PYTHON) -m compileall backend config

build-frontend:
	cd frontend && npm run build

build: compile build-frontend

test:
	$(PYTHON) -m pytest -v

lint:
	$(PYTHON) -m ruff check backend config

fmt:
	$(PYTHON) -m ruff format backend config

freeze:
	$(PYTHON) -m pip freeze > requirements.txt

# Database / integration ----------------------------------------------------

migrate:
	$(PYTHON) scripts/run_migrations.py

seed:
	$(PYTHON) scripts/seed_faqs.py

smoke-mcp:
	$(PYTHON) scripts/smoke_test_mcp.py

# Docker --------------------------------------------------------------------

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

# Cleanup -------------------------------------------------------------------

clean:
	@if exist .venv rmdir /s /q .venv
	@if exist frontend\node_modules rmdir /s /q frontend\node_modules
