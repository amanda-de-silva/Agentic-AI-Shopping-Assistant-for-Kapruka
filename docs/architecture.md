# Kapruka AI Shopping Concierge — System Architecture

Turning "Mini Project 03" into a real, general-purpose conversational commerce agent for Kapruka: not just gifting, but search → recommend → cart → checkout → track, done entirely through chat.

---

## 1. What changes vs. the course brief, and why

| Course brief | This build | Reason |
|---|---|---|
| Playwright crawler → `catalog.json` | **Kapruka's official MCP server** (`https://mcp.kapruka.com/mcp`) | Kapruka now exposes `search_products`, `get_product`, `list_categories`, `list_delivery_cities`, `check_delivery`, `create_order`, `track_order` live. Scraping would give stale, duplicate data you'd have to maintain — the MCP server is the source of truth (real-time price/stock). |
| Qdrant used only for product-catalog RAG | Qdrant used for **semantic memory recall** (recipient profiles, free-text preferences, past-conversation gist) | Product search is already delegated to Kapruka's `search_products` tool — no need to re-embed their catalog. Qdrant's real job here is fuzzy recall: "get something for mom" → vector search over stored notes about "mom".|
| Scope limited to gifting | General shopping copilot (search, compare, cart, checkout, delivery check, order tracking) with gifting as one specialized flow | Your ask — gifting becomes one *intent*, not the whole app. |
| No frameworks allowed (course rule) | Custom lightweight **async orchestrator** (still no LangGraph/CrewAI) | That course restriction is gone, but a hand-rolled orchestrator is genuinely the better choice here: full control over Langfuse tracing, easy `asyncio.gather()` parallelism, no framework overhead for what is really 6 tools + a router. (If you ever want built-in state-machine visualization, LangGraph is a drop-in alternative later — the agent interfaces below are framework-agnostic on purpose.) |
| Single deliverable script/notebook | Production-shaped service: FastAPI backend, React frontend, Postgres (Supabase) for structured state, Qdrant Cloud for semantic recall, Langfuse for tracing | "Real business product" requirement. |

Key architectural decision that the course brief doesn't address at all: **Kapruka's MCP has no cart or session concept** — `create_order` takes the whole cart as one payload and returns a pay link. So *your app owns the cart*. This is the central design fact everything else hangs off of.

---

## 2. High-level architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  FRONTEND (React + TS)                                              │
│  Chat UI · Product cards · Cart drawer · Checkout · Order tracker   │
└───────────────────────────────┬───────────────────────────────────┘
                                 │ REST + SSE/WebSocket
┌───────────────────────────────▼───────────────────────────────────┐
│  BACKEND (FastAPI, async)                                          │
│                                                                     │
│   API layer  →  ORCHESTRATOR  →  Response Composer  →  UI Actions  │
│                     │                                               │
│         ┌───────────┼─────────────────────────────┐                │
│         ▼           ▼               ▼              ▼                │
│    Router Agent  Memory Agent   Reflection Agent   (all run async,  │
│         │        (parallel        (only when a       gathered      │
│         │        prefetch)         recipient is      concurrently) │
│         ▼                          involved)                       │
│   ┌─────────────┬──────────────┬──────────────┬─────────────┐      │
│   │ Catalog     │ Logistics    │ Cart/Checkout│ Order        │      │
│   │ Agent       │ Agent        │ Agent        │ Agent        │      │
│   │ (MCP: search│ (MCP: cities,│ (internal    │ (MCP:        │      │
│   │ /get/list-  │ check_       │ cart state + │ track_order) │      │
│   │ categories) │ delivery)    │ MCP: create_ │              │      │
│   │             │              │ order)       │              │      │
│   └──────┬──────┴──────┬───────┴──────┬───────┴──────┬───────┘      │
│          └─────────────┴──────────────┴──────────────┘              │
│                          Kapruka MCP Client (rate-limited, retried) │
└───────────────────────────────┬───────────────────────────────────┘
                                 │
        ┌────────────────────────┼───────────────────────┬───────────────┐
        ▼                        ▼                        ▼               ▼
 Kapruka MCP Server      Supabase (Postgres)        Qdrant Cloud     Langfuse
 mcp.kapruka.com/mcp     sessions, messages,       semantic_memory,  traces for every
 (no auth, 60 req/min,   cart, orders, recipient   convo_summaries   agent + tool +
 30 orders/hr per IP)    profiles, order cache      collections      LLM call
                                                          ▲
                                                          │
                                            OpenRouter (openai/gpt-4o-mini for
                                            chat/router/reflection, openai/
                                            text-embedding-3-small for embeddings)
```

---

## 3. Agent design

### 3.1 Router Agent
Single fast LLM call (low temperature) that classifies the user turn into one or more intents and extracts slots:
`product_search`, `product_detail`, `delivery_check`, `add_to_cart`, `remove_from_cart`, `view_cart`, `checkout`, `track_order`, `save_preference` (allergy/favorite/recipient info), `general_chat`.

It runs **concurrently** with a memory prefetch (short-term window + a Qdrant similarity search seeded from the raw user message) so the specialist agents receive context the instant routing finishes — no serial "route, then remember" round trip.

### 3.2 Specialist agents (Kapruka MCP–backed)
- **Catalog Agent** → `kapruka_search_products`, `kapruka_get_product`, `kapruka_list_categories`
- **Logistics Agent** → `kapruka_list_delivery_cities`, `kapruka_check_delivery`
- **Order Agent** → `kapruka_track_order`
- **Cart/Checkout Agent** → owns your internal cart table; on checkout, assembles the cart into Kapruka's `create_order` payload (`cart`, `recipient`, `delivery`, `sender`, `gift_message`, `currency`) and gets back the click-to-pay URL

When a turn needs more than one (e.g., "find a nut-free cake and check if it can reach Kandy by Sunday"), the orchestrator runs Catalog + Logistics **in parallel** via `asyncio.gather`, not sequentially.

### 3.3 Memory Agent
Reads/writes three tiers:
1. **Short-term** — rolling window of the last N turns for the session (Supabase `messages` table, queried by `session_id`, capped by `params.yaml: memory.short_term_window`).
2. **Structured long-term** — `recipient_profiles` table in Supabase: name, relationship, allergies (array), likes/dislikes, notes, last-updated. This is the ground truth for hard constraints like allergies — never trust a vector match alone for a safety-critical fact.
3. **Semantic long-term (Qdrant)** — embeddings of free-text notes and conversation summaries, so "the usual for my wife" or "something like what I got my mom last time" resolves without exact keyword matches. Qdrant returns candidate profile IDs; the agent then pulls the authoritative record from Supabase before using it.

### 3.4 Reflection Agent (Draft → Reflect → Revise)
Only triggered for `gift_recommendation` and `add_to_cart`-for-a-named-recipient intents (kept out of the hot path for plain "show me headphones" queries, to protect latency):
1. **Draft** — Catalog Agent's candidate item(s).
2. **Reflect** — critique against the recipient's structured profile (allergies first, then preferences).
3. **Revise** — if a violation is found, re-query the Catalog Agent with adjusted filters; capped at `params.yaml: reflection.max_revise_iterations` revisions before falling back to asking the user.

### 3.5 Response Composer
Merges whatever specialist outputs came back into one natural chat reply **and** emits a structured `ui_actions` array the frontend consumes, e.g.:
```json
{
  "reply": "Added the nut-free chocolate cake to your cart — want me to check delivery to Kandy?",
  "ui_actions": [
    { "type": "cart_updated", "cart": { "items": [...], "item_count": 3 } }
  ]
}
```
This is what makes "add to cart" / "go to checkout" *actually* move the UI instead of just describing it.

---

## 4. Data model (Supabase / Postgres)

```
sessions(id, user_id nullable, created_at, last_active_at, channel)
messages(id, session_id fk, role, content, intent, created_at)
recipient_profiles(id, user_id fk, name, relationship, allergies text[], likes text[], dislikes text[], notes, created_at, updated_at)
cart_items(id, session_id fk, product_id, name, price, currency, qty, recipient_id fk nullable, gift_message, added_at)
orders(id, session_id fk, kapruka_order_number, status, checkout_url, total, currency, created_at)
order_status_cache(order_number pk, last_status, last_synced_at, raw_payload jsonb)
delivery_cache(city_query pk, resolved_city, matched_at)   -- short TTL cache for list_delivery_cities
```

Qdrant collections:
```
semantic_memory        # vectors of recipient notes / preference snippets, payload: {profile_id, user_id, text}
conversation_summaries # vectors of periodic session summaries, payload: {session_id, summary, created_at}
```

---

## 5. Config strategy

Three YAML files as you asked, each with a single responsibility:

**config/models.yaml** — which model does what
```yaml
llm:
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  chat_model: openai/gpt-4o-mini
  router_model: openai/gpt-4o-mini
  reflection_model: openai/gpt-4o-mini
  composer_model: openai/gpt-4o-mini
  embedding_model: openai/text-embedding-3-small
  temperature:
    router: 0.0
    catalog_agent: 0.3
    logistics_agent: 0.2
    reflection: 0.1
    composer: 0.5
  max_tokens:
    router: 300
    agent: 800
    reflection: 500
    composer: 600
  timeout_seconds: 30
```

**config/params.yaml** — runtime/behavioral tuning
```yaml
memory:
  short_term_window: 12
  short_term_ttl_minutes: 120
  semantic_top_k: 5
  semantic_score_threshold: 0.75
cart:
  max_items: 50
  price_lock_minutes: 60          # mirrors Kapruka's 60-min price lock
mcp:
  base_url: https://mcp.kapruka.com/mcp
  requests_per_minute: 60          # matches Kapruka's published limit
  orders_per_hour: 30              # matches Kapruka's published limit
  timeout_seconds: 15
  retry:
    max_attempts: 3
    backoff_seconds: 1.5
reflection:
  enabled_for_intents: ["gift_recommendation", "add_to_cart_for_recipient"]
  max_revise_iterations: 2
observability:
  langfuse_enabled: true
  sample_rate: 1.0
```

**config/faqs.yaml** — canned answers for non-agentic questions (shipping policy, returns, payment methods) so you don't burn an LLM call + tool round trip on things that never change.

**config/mcp_tools.yaml** — a local reference copy of the 7 Kapruka tool names/params (for prompt-building and docs). The live schema is still always fetched from the MCP server at startup — this file is documentation, not the source of truth.

---

## 6. Parallelization plan (where latency actually gets saved)

- Router classification **∥** short-term + semantic memory prefetch
- Catalog Agent **∥** Logistics Agent, whenever both are implicated in one turn
- Reflection's "Reflect" step only re-runs the Catalog Agent for the *specific* violating slot, not a full fresh search
- MCP calls that don't depend on each other (e.g., `get_product` for 3 candidate SKUs) are gathered, not looped
- Qdrant + Supabase profile lookups run together, with Supabase treated as the tie-breaker/ground truth for allergy-type facts if they ever disagree

All of this is orchestrated with `asyncio.gather()` inside `agents/orchestrator.py` — no external workflow engine needed at this scale.

---

## 7. Frontend shape (React + TypeScript + Vite)

- Chat pane with streaming replies (SSE) and a "thinking / memory recalled" indicator (the bonus feature from the course brief, now genuinely useful — it shows the user *why* the agent knows about their mother's nut allergy).
- Product cards rendered directly from `search_products` / `get_product` MCP output.
- Cart drawer reflects `ui_actions` pushed from the backend the instant an item is added — no manual refresh.
- Checkout page receives the Kapruka click-to-pay URL and can either redirect or embed it.
- Order tracking page polls `track_order` through your backend (which itself can cache short-TTL results in `order_status_cache`).

---

## 8. Complete folder structure

Legend: **[H]** = you create/edit this by hand · **[A]** = generated automatically by a tool/command · **[G]** = git-ignored, never committed

```
kapruka-ai-concierge/
├── .env.example                                    [H]
├── .env                                             [H][G]
├── .gitignore                                       [H]
├── Makefile                                         [H]
├── README.md                                        [H]
├── docker-compose.yml                               [H]
├── pyproject.toml                                   [H]
├── uv.lock                                           [A][G]   (generated by `uv`/poetry lockfile)
├── requirements.txt                                  [A]      (exported from pyproject, `make freeze`)
│
├── config/
│   ├── __init__.py                                   [H]  (empty)
│   ├── settings.py                                    [H]  (pydantic Settings: loads .env + yaml)
│   ├── models.yaml                                    [H]
│   ├── params.yaml                                    [H]
│   ├── faqs.yaml                                       [H]
│   └── mcp_tools.yaml                                  [H]
│
├── backend/
│   ├── __init__.py                                    [H]  (empty)
│   ├── main.py                                        [H]  (FastAPI app factory + startup: MCP handshake, Qdrant/Supabase clients, Langfuse init)
│   │
│   ├── api/
│   │   ├── __init__.py                                [H]
│   │   ├── deps.py                                    [H]  (FastAPI dependency injectors: session, db, mcp client)
│   │   ├── routes/
│   │   │   ├── __init__.py                            [H]
│   │   │   ├── chat.py                                [H]  (POST /chat — non-streaming)
│   │   │   ├── cart.py                                 [H]  (GET/PATCH /cart)
│   │   │   ├── checkout.py                             [H]  (POST /checkout)
│   │   │   ├── orders.py                               [H]  (GET /orders/{order_number})
│   │   │   ├── memory.py                               [H]  (CRUD /recipients — manual profile edit UI hook)
│   │   │   ├── delivery.py                             [H]  (GET /delivery/check)
│   │   │   └── health.py                               [H]  (GET /health, /ready)
│   │   └── websocket/
│   │       ├── __init__.py                            [H]
│   │       └── chat_stream.py                          [H]  (WS/SSE streaming chat endpoint)
│   │
│   ├── agents/
│   │   ├── __init__.py                                 [H]
│   │   ├── orchestrator.py                              [H]  (turn entrypoint: gather router+memory, dispatch specialists, run reflection, call composer)
│   │   ├── router_agent.py                               [H]
│   │   ├── catalog_agent.py                              [H]
│   │   ├── logistics_agent.py                            [H]
│   │   ├── cart_agent.py                                  [H]
│   │   ├── order_agent.py                                  [H]
│   │   ├── memory_agent.py                                 [H]
│   │   ├── reflection_agent.py                              [H]
│   │   └── response_composer.py                             [H]
│   │
│   ├── mcp/
│   │   ├── __init__.py                                 [H]
│   │   ├── client.py                                    [H]  (async MCP streamable-HTTP client wrapping the 7 Kapruka tools)
│   │   ├── tool_schemas.py                               [H]  (pydantic models mirroring each tool's params)
│   │   └── rate_limiter.py                                [H]  (token-bucket for 60 req/min & 30 orders/hr)
│   │
│   ├── llm/
│   │   ├── __init__.py                                 [H]
│   │   ├── openrouter_client.py                          [H]
│   │   ├── embeddings_client.py                           [H]
│   │   └── prompt_loader.py                               [H]  (loads/renders the .jinja prompt files)
│   │
│   ├── prompts/
│   │   ├── router_system.jinja                          [H]
│   │   ├── catalog_agent_system.jinja                     [H]
│   │   ├── logistics_agent_system.jinja                   [H]
│   │   ├── cart_agent_system.jinja                         [H]
│   │   ├── memory_agent_system.jinja                        [H]
│   │   ├── reflection_system.jinja                          [H]
│   │   └── response_composer_system.jinja                    [H]
│   │
│   ├── memory/
│   │   ├── __init__.py                                  [H]
│   │   ├── short_term.py                                 [H]  (session message window)
│   │   ├── semantic_memory.py                             [H]  (Qdrant read/write)
│   │   └── structured_memory.py                            [H]  (Supabase recipient_profiles read/write)
│   │
│   ├── db/
│   │   ├── __init__.py                                  [H]
│   │   ├── supabase_client.py                             [H]
│   │   ├── qdrant_client.py                                [H]
│   │   ├── models.py                                        [H]  (SQLAlchemy/pydantic row models)
│   │   ├── repositories/
│   │   │   ├── __init__.py                              [H]
│   │   │   ├── session_repo.py                            [H]
│   │   │   ├── message_repo.py                             [H]
│   │   │   ├── cart_repo.py                                 [H]
│   │   │   ├── order_repo.py                                 [H]
│   │   │   └── profile_repo.py                                [H]
│   │   └── migrations/
│   │       ├── 0001_init.sql                             [H]
│   │       ├── 0002_cart.sql                               [H]
│   │       ├── 0003_orders.sql                               [H]
│   │       └── 0004_recipient_profiles.sql                    [H]
│   │
│   ├── services/
│   │   ├── __init__.py                                  [H]
│   │   ├── product_service.py                             [H]  (thin business layer over catalog_agent + caching)
│   │   ├── cart_service.py                                 [H]
│   │   ├── checkout_service.py                              [H]
│   │   ├── delivery_service.py                               [H]
│   │   └── order_tracking_service.py                          [H]
│   │
│   ├── schemas/
│   │   ├── __init__.py                                  [H]
│   │   ├── chat.py                                        [H]
│   │   ├── cart.py                                         [H]
│   │   ├── order.py                                        [H]
│   │   ├── product.py                                       [H]
│   │   ├── profile.py                                        [H]
│   │   └── ui_actions.py                                      [H]
│   │
│   ├── observability/
│   │   ├── __init__.py                                  [H]
│   │   ├── langfuse_client.py                             [H]
│   │   └── tracing.py                                      [H]  (decorator used on every agent/tool/LLM call)
│   │
│   ├── core/
│   │   ├── __init__.py                                  [H]
│   │   ├── exceptions.py                                  [H]
│   │   ├── logging.py                                      [H]
│   │   └── cache.py                                         [H]  (in-memory TTL cache for categories/delivery cities)
│   │
│   └── utils/
│       ├── __init__.py                                  [H]
│       ├── async_utils.py                                 [H]  (gather-with-timeout helpers)
│       └── text_utils.py                                    [H]
│
├── scripts/
│   ├── seed_faqs.py                                     [H]
│   ├── run_migrations.py                                 [H]
│   └── smoke_test_mcp.py                                   [H]  (pings all 7 Kapruka tools once, prints latency)
│
├── tests/
│   ├── __init__.py                                      [H]
│   ├── conftest.py                                        [H]
│   ├── test_router_agent.py                                [H]
│   ├── test_cart_service.py                                  [H]
│   ├── test_mcp_client.py                                     [H]
│   └── test_reflection_agent.py                                [H]
│
├── frontend/
│   ├── package.json                                     [H]
│   ├── package-lock.json                                  [A]  (npm install)
│   ├── tsconfig.json                                       [H]
│   ├── vite.config.ts                                       [H]
│   ├── index.html                                            [H]
│   ├── .env                                                   [H][G]
│   ├── .env.example                                            [H]
│   ├── node_modules/                                            [A][G]
│   ├── public/
│   │   └── favicon.svg                                    [H]
│   └── src/
│       ├── main.tsx                                        [H]
│       ├── App.tsx                                          [H]
│       ├── api/
│       │   ├── client.ts                                    [H]
│       │   ├── chat.ts                                        [H]
│       │   ├── cart.ts                                          [H]
│       │   └── orders.ts                                          [H]
│       ├── store/
│       │   ├── chatStore.ts                                   [H]  (zustand)
│       │   ├── cartStore.ts                                     [H]
│       │   └── sessionStore.ts                                    [H]
│       ├── components/
│       │   ├── chat/
│       │   │   ├── ChatWindow.tsx                             [H]
│       │   │   ├── MessageBubble.tsx                            [H]
│       │   │   ├── ThinkingIndicator.tsx                          [H]
│       │   │   └── MemoryRecallBadge.tsx                            [H]
│       │   ├── products/
│       │   │   ├── ProductCard.tsx                              [H]
│       │   │   └── ProductGrid.tsx                                 [H]
│       │   ├── cart/
│       │   │   ├── CartDrawer.tsx                                [H]
│       │   │   └── CartItemRow.tsx                                 [H]
│       │   └── checkout/
│       │       ├── CheckoutSummary.tsx                             [H]
│       │       └── OrderTracker.tsx                                  [H]
│       ├── pages/
│       │   ├── HomePage.tsx                                    [H]
│       │   ├── CartPage.tsx                                      [H]
│       │   ├── CheckoutPage.tsx                                    [H]
│       │   └── OrderTrackingPage.tsx                                 [H]
│       ├── hooks/
│       │   ├── useChatStream.ts                                [H]
│       │   └── useCart.ts                                        [H]
│       └── styles/
│           └── global.css                                     [H]
│
├── .venv/                                                       [A][G]  (`uv venv`)
├── docs/
│   └── architecture.md                                          [H]  (this document, kept in-repo)
└── .github/
    └── workflows/
        └── ci.yml                                               [H]  (lint + test on push — optional but recommended)
```

**Rule of thumb:** every `__init__.py`, every `.py`, every `.tsx`/`.ts`, every `.yaml`/`.sql`/`.jinja` you see marked **[H]** is something you (or Claude Code, working file-by-file) writes. The only things that appear on disk without you writing them are lockfiles, `node_modules/`, `.venv/`, and `requirements.txt` if you choose to export it — everything else is deliberate, hand-authored code, which is what "real-world standard" actually means: nothing in this tree is a mystery generated artifact except dependency locks.

---

## 9. `.env.example`

```
# LLM (OpenRouter)
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Vector DB (Qdrant Cloud)
QDRANT_URL=
QDRANT_API_KEY=
QDRANT_COLLECTION_SEMANTIC_MEMORY=semantic_memory
QDRANT_COLLECTION_CONVO_SUMMARIES=conversation_summaries

# Database (Supabase)
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_ANON_KEY=

# Observability (Langfuse)
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

# Kapruka MCP
KAPRUKA_MCP_URL=https://mcp.kapruka.com/mcp

# App
APP_ENV=development
BACKEND_PORT=8000
FRONTEND_PORT=5173
CORS_ORIGINS=http://localhost:5173
SESSION_SECRET=
```

---

## 10. Makefile

```makefile
.PHONY: install install-backend install-frontend dev dev-backend dev-frontend migrate seed smoke-mcp test lint fmt clean

install: install-backend install-frontend

install-backend:
	uv venv .venv
	uv pip install -r requirements.txt

install-frontend:
	cd frontend && npm install

dev:
	@echo "Run 'make dev-backend' and 'make dev-frontend' in two separate terminals on Windows."

dev-backend:
	uv run uvicorn backend.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

migrate:
	uv run python scripts/run_migrations.py

seed:
	uv run python scripts/seed_faqs.py

smoke-mcp:
	uv run python scripts/smoke_test_mcp.py

test:
	uv run pytest -v

lint:
	uv run ruff check backend

fmt:
	uv run ruff format backend

clean:
	rm -rf .venv frontend/node_modules
```

**Windows/PowerShell note:** `make` isn't installed by default on Windows. Easiest fix: `choco install make` (Chocolatey) or `scoop install make`. GNU Make's `-j` parallel job runner behaves inconsistently in PowerShell, which is why `make dev` above just tells you to run backend/frontend in two terminals rather than trying to background-process them — cleaner than fighting Make's process model on Windows. If you'd rather have one command spin up both, I can wire `concurrently` (npm) into a `make dev` target instead — say the word and I'll add it.

---

## 11. Suggested build order

1. `backend/mcp/client.py` + `scripts/smoke_test_mcp.py` — prove you can call all 7 Kapruka tools before anything else exists.
2. Supabase migrations + `db/repositories` — get sessions/messages/cart/profiles persisting.
3. `router_agent.py` + `orchestrator.py` with just Catalog Agent wired in — a working "search and talk about products" loop.
4. `cart_agent.py` + `checkout_service.py` — closes the loop to a real Kapruka pay link.
5. `memory_agent.py` (Supabase first, Qdrant second) — then `reflection_agent.py` on top of it.
6. `logistics_agent.py` + `order_agent.py` — round out the tool coverage.
7. Langfuse tracing on every layer, then the React frontend, streaming last.

This order gets you an end-to-end "search → cart → checkout" demo (steps 1–4) before you invest in the memory/safety layer, which is usually the highest-value order to build in for a real product.
