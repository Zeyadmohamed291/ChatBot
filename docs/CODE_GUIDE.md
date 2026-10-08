# 🛠️ Technical Code & Architecture Guide

A comprehensive technical manual for developers maintaining or extending the **Loulou AI** shopping assistant microservice.

---

## 1. Architecture & Component Interaction

The microservice follows a clean layered design separating HTTP transport, business orchestration, AI intent planning, data retrieval gateways, and stateful session storage.

```
                  ┌───────────────────────────────┐
                  │       Laravel Backend         │
                  └──────────────┬────────────────┘
                                 │ HTTP (Bearer Token + Headers)
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                          FastAPI Layer                          │
│   app/main.py (Lifespan, Auth, Rate Limiter, Endpoints)         │
└────────────────────────────────┬────────────────────────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Orchestration Layer                        │
│   app/service.py (Dialog Flow, Invariants, Slot Resolution)     │
└───────┬────────────────────────┼────────────────────────┬───────┘
        ▼                        ▼                        ▼
┌──────────────┐         ┌──────────────┐         ┌───────────────┐
│ Session Mgmt │         │Intent Planner│         │ Data Gateway  │
│app/sessions.py│         │app/planner.py│         │app/gateway.py │
│(Memory/Redis)│         │(OpenAI/Demo) │         │(Mock/Laravel) │
└──────────────┘         └──────────────┘         └───────────────┘
```

---

## 2. Detailed Module Breakdown

### 2.1 `app/main.py` — Entrypoint & Transport Security
* **`create_app()`**: FastAPI factory setting up dependency injection, lifespan context manager, CORS (restricted to internal), and exception handlers.
* **Authentication Middleware**: Verifies `AI_SERVICE_TOKEN` constant-time comparison.
* **Session Headers Extraction**: Enforces mandatory `X-Session-Subject` and extracts optional `X-Customer-ID`.
* **Rate Limiting**: Per-subject token bucket rate limiter to shield the AI model from flooding attacks.
* **Endpoints**:
  - `POST /v1/chat`: Main conversational endpoint.
  - `DELETE /v1/sessions/{session_id}`: Explicit session invalidation.
  - `GET /health/live`: Liveness probe for orchestrators (Kubernetes / Docker Compose).
  - `GET /health/ready`: Readiness probe verifying Redis connectivity and gateway health.

### 2.2 `app/service.py` — Core Business Orchestrator
The central coordinator that executes the conversation turn:
* Validates user message constraints (length, sanitization via `app/text.py`).
* Retrieves or creates the session from `SessionStore`.
* Invokes `Planner` with conversation turn history.
* **Security Guard on Orders**: Verifies that any order number query was literally provided in the user's message (preventing hallucinated lookups), checks authentication status, and executes order retrieval with ownership constraints.
* Queries `Gateway` for products, stores, or FAQs based on planned intent.
* Synthesizes final text, product cards, store cards, quick replies, and WhatsApp handoff using `app/replies.py`.
* Updates sliding-window conversation turns in session storage.

### 2.3 `app/planner.py` — Intent Planning & Slot Extraction
Translates natural language text into a structured `PlannerDecision`:
* **OpenAI Mode**: Uses `OpenAI` client with Function Calling / Structured Outputs via standard tool schemas (`search_products`, `get_store`, `get_order_status`, `answer_faq`, `escalate_to_human`, `chitchat`). Sets `store=false` to protect customer privacy on OpenAI infrastructure.
* **Demo Mode**: Fast, deterministic regex- and keyword-based classifier that simulates real tool calls for local zero-cost testing.

### 2.4 `app/gateway.py` — Backend Data Access Layer
Abstracts catalog and order lookups behind the `CatalogGateway` protocol:
* **`MockGateway`**: In-memory catalog parser reading `app/data/catalog.json` with filtering by store, price, and keyword query matching.
* **`LaravelGateway`**: Asynchronous HTTP client communicating with Laravel's `/api/ai/*` endpoints with connection pooling, retries, and domain whitelisting for catalog and tracking URLs.

### 2.5 `app/sessions.py` — Ephemeral Session Management
Manages stateful conversation context with security isolation:
* **Session Model**: Holds `session_id`, `subject`, `customer_id`, timestamp, and a sliding window of recent conversation turns (`TurnRecord`).
* **Isolation Guarantee**: Prevents cross-tenant access. If subject or customer identity mismatches, session access is denied (`404`).
* **`InMemorySessionStore`**: Mutex-guarded in-memory dictionary for single-instance development.
* **`RedisSessionStore`**: Distributed Redis store leveraging native Redis key expiration (`EXPIRE`) for sliding TTL.

### 2.6 `app/models.py` — Domain Models & Type Safety
Pydantic v2 schemas:
* `ChatRequest` / `ChatResponse`: External API contract payloads.
* `ProductCard` / `StoreCard`: Structured UI widgets rendered on the client.
* `OrderStatus`: Customer order state model.
* `PlannerDecision`: Internal structured output from OpenAI Function Calling.

### 2.7 `app/replies.py` & `app/text.py` — Localization & NLP
* `app/replies.py`: Pre-approved bilingual templates for greetings, fallbacks, order status summaries, and WhatsApp links.
* `app/text.py`: Arabic character normalization (e.g. normalizing `أ`, `إ`, `آ` to `ا`, stripping tatweel and harakat), language detection, and alphanumeric cleanup.

### 2.8 `app/config.py` — Strongly Typed Settings
Loads `.env` parameters using `pydantic-settings`:
* Validates allowed hostnames (`ALLOWED_CATALOG_HOSTS`, `ALLOWED_TRACKING_HOSTS`).
* Validates timeouts, token limits, and operational modes.

---

## 3. Extending the Microservice

### Adding a New Tool / Intent
1. Add the tool definition schema in `app/planner.py`.
2. Update the `PlannerDecision` union or enum in `app/models.py`.
3. Handle the decision type in `app/service.py` to call the appropriate gateway method or template.
4. Add corresponding test cases in `tests/test_chat.py`.

### Connecting a Real Laravel Backend
1. Deploy Laravel read endpoints as documented in [`docs/API_CONTRACT.md`](API_CONTRACT.md).
2. Set `BACKEND_MODE=laravel`, `BACKEND_BASE_URL=https://...`, and `BACKEND_API_TOKEN=...` in `.env`.
3. Verify allowed hostnames in `ALLOWED_CATALOG_HOSTS`.
4. Run `python scripts/evaluate.py` or `python scripts/demo.py` to verify live integration.
