# Kapruka AI Shopping Concierge

A conversational shopping copilot for Kapruka.com. Search, get recommendations, manage a
cart, check delivery feasibility, checkout, and track orders — all through chat. Built on
Kapruka's official MCP server (no scraping), OpenRouter for LLM access, Qdrant Cloud for
semantic memory, Supabase for structured state, and Langfuse for observability.

See `docs/architecture.md` for the full system design and rationale.

## Quick start (Windows / PowerShell)

```powershell
# 1. Install deps
make install

# 2. Fill in .env (OpenRouter key, Supabase project, Qdrant Cloud, Langfuse keys)
notepad .env

# 3. Run DB migrations against your Supabase project
make migrate

# 4. Sanity check the Kapruka MCP connection
make smoke-mcp

# 5. Run backend and frontend in two separate terminals
make dev-backend
make dev-frontend
```

Backend: http://localhost:8000/docs (FastAPI Swagger UI)
Frontend: http://localhost:5173

## Requirements
- Python 3.11+
- Node.js 20+
- A Supabase project (Postgres) — free tier is fine
- A Qdrant Cloud cluster — free tier is fine
- An OpenRouter API key
- A Langfuse account (cloud or self-hosted)

`make` is not installed on Windows by default. Install it with `choco install make` or
`scoop install make`, or run the underlying commands from the Makefile directly in
PowerShell.
