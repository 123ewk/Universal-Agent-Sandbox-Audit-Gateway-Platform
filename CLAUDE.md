# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: ShadowOS — Universal Agent Sandbox & Audit Gateway

An AI Agent runtime with browser automation, audit gateway, and real-time frontend.  
**Project name**: `ShadowOS` | **Dev requirements**: Python 3.11+, Node 18+, PostgreSQL 16+, Redis 7+

## Common Commands

```bash
# Backend (backend/ directory)
cd backend && uvicorn app.main:app --reload --port 8000          # Dev server (hot reload)
cd backend && pytest -xvs                                       # Run all tests
cd backend && pytest -xvs tests/test_runtime.py                  # Run single test file
cd backend && pytest -xvs -k "test_approval_manager"             # Run tests matching keyword
cd backend && alembic upgrade head                               # Run DB migrations

# Frontend (frontend/ directory)
cd frontend && npm run dev                                       # Vite dev server (port 5173, proxies /api -> :8000)
cd frontend && npm run build                                     # Type-check + production build
cd frontend && npx vue-tsc --noEmit                              # TypeScript check only
```

**Note**: Frontend dev server proxies `/api` (incl. WebSocket) to `http://127.0.0.1:8000` — run both backend and frontend for full stack.

## Architecture

### Backend Pipeline (data flow)

```
HTTP POST /api/v1/tasks
  → TaskManager.start()          # Background asyncio task
    → AgentRuntime.run()         # Lifecycle events via EventBus
      → AgentGraph.invoke()      # LangGraph StateGraph loop
        → Plan Node              # LLM decomposes task → steps
        → Execute Node           # AuditGateway.invoke(skill)
          → RiskEngine.assess()  # L1-L5 scoring + dynamic param analysis
          → AuditLog (DB)        # Every call logged
          → Approval?             # L4: wait human approval (asyncio.Event)
          → Skill.execute()       # Actual browser/file/shell operation
        → Observe Node           # ObservationPipeline processes results
        → Reflect Node           # LLM decides: continue/retry/replan/complete/abort
      → EventBus.dispatch()      # Pub/sub: WS broadcast + DB event log
```

### Key Layers

- **AuditGateway** (`backend/app/engine/gateway.py`) — Central security checkpoint. *Every* skill call **must** pass through this. No direct `skill.execute()`.
- **RiskEngine** (`backend/app/engine/risk.py`) — L1-L5 scoring (1-100). Static (skill declaration) + dynamic (parameter keywords, URL domain) analysis. L5 direct block, L4 requires approval, L1-L3 pass.
- **AgentGraph** (`backend/app/agent/graph.py`) — LangGraph `Plan → Execute → Observe → Reflect` loop. Uses `StateGraph` with conditional edges. Auto-continues on success (no LLM call), auto-replans on 3 consecutive failures.
- **EventBus** (`backend/app/runtime/event_bus.py`) — Singleton pub/sub. Subscribers: `ws` (WebSocketManager.broadcast), `db` (SessionManager event log). Per-session seq dedup.
- **SandboxEngine** (`backend/app/sandbox/engine.py`) — Wraps Playwright `Page`. Each session gets independent `BrowserContext`. URL blocklist + route interception via `SandboxSecurity`.
- **SkillRegistry** (`backend/app/skills/registry.py`) — Singleton. Auto-discovers all `BaseSkill` subclasses at startup. Skills categorized by `SkillTier` (CORE/INTERACTION/FILE/SHELL) for progressive disclosure.
- **ApprovalManager** (`backend/app/audit/approval.py`) — `asyncio.Event`-based non-blocking suspend. Agent `await request.event.wait()` → frontend approves/denies → `event.set()` unblocks agent.
- **TaskManager** (`backend/app/runtime/task_manager.py`) — Wraps `asyncio.create_task`. HTTP returns 202 immediately, agent runs in background.

### Backend Packages

| Package | Responsibility |
|---|---|
| `app/agent/` | LangGraph agent (graph, prompts, state, context, compression, observation, LLM client) |
| `app/engine/` | Audit gateway + risk engine (the security core) |
| `app/sandbox/` | Playwright-based browser sandbox (engine, provider, security, screenshots) |
| `app/skills/` | Skill system (base class, registry, concrete skills: browser, shell, file) |
| `app/runtime/` | Runtime layer (EventBus, TaskManager, SessionManager, WebSocketManager, AgentRuntime) |
| `app/audit/` | Human approval workflow + audit routes |
| `app/models/` | SQLAlchemy ORM models (session, audit_log, approval, memory) |
| `app/schemas/` | Pydantic response schemas |
| `app/ws/` | WebSocket protocol & manager |
| `app/middleware/` | CORS, RequestID middleware |
| `app/migrations/` | Alembic DB migrations |

### Frontend Architecture (Vue 3 + TypeScript)

```
WSClient → EventBus → Reducers → Pinia Stores → Vue Components
```

- **WSClient** (`frontend/src/runtime/ws-client.ts`) — WebSocket with exponential backoff reconnect (1s → 2s → 4s → max 30s)
- **EventBus** (`frontend/src/runtime/event-bus.ts`) — Client-side pub/sub, routes WS messages by event prefix (`agent.*` → step reducer, `sandbox.*` → sandbox reducer, `approval.*` → approval reducer)
- **Reducers** (`frontend/src/runtime/reducers/`) — Pure state transforms: `session.reducer.ts`, `step.reducer.ts`, `approval.reducer.ts`, `sandbox.reducer.ts`
- **Stores** (`frontend/src/stores/`) — Pinia stores: `sessions.ts` (Map<sessionId, SessionState>), `approvals.ts` (approval requests)
- **Views** — `TaskCreate.vue` (input task), `MonitorView.vue` (live execution), `HistoryView.vue`, `ReplayView.vue`

### Key Patterns

- **Singleton managers** — `EventBus`, `SessionManager`, `TaskManager`, `AuditGateway`, `SkillRegistry`, `WebSocketManager` all use `get_xxx()` global accessors
- **No bare dict returns** — Use dataclasses (`LLMResponse`, `SkillResult`, `ActionResult`, `PageInfo`, `RiskAssessment`, `ApprovalRequest`)
- **Pgvector readiness** — SQLAlchemy models include embedding fields; pgvector in requirements
- **Async everywhere** — `asyncio` throughout backend; `asyncpg` for PostgreSQL; `httpx` for test client
- **Alembic for migrations** — Single migration `e15a188c1e58` at initialization

### Config

All config in `backend/app/config.py` via `pydantic-settings`. Reads from `.env` (see `backend/.env.example`). Key: LLM provider (openai/deepseek/claude), DB/Redis connection, sandbox mode (local/docker), security settings (blocklist, allowlist, high-risk domains).

### Testing

- `backend/pytest.ini`: `asyncio_mode = auto` — test async functions work without `@pytest.mark.asyncio`
- Test files use `httpx.AsyncClient` with `ASGITransport` for FastAPI integration tests
- `conftest.py` provides fixtures for test app, DB session, etc.

## Development Style

- **Modular, single-responsibility** — One function/class per focused task. No monolithic files mixing routes, middleware, and DB logic.
- **Async I/O** — Every I/O operation (network, file, DB, Playwright) must use `async/await`.
- **Strong typing** — Python: full type hints. TypeScript: strict mode via `vue-tsc`. Avoid `any`/`dict` return types; prefer dataclasses.
- **Defensive** — All I/O code wrapped in `try-except/try-catch` with logging. No unhandled crashes.
- **Singleton accessors** — All global state uses `get_xxx()` functions (not class-level globals or `from module import instance`).
