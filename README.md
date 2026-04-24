<p align="center">
  <img src="docs/assets/cover.png" alt="Model Maestro" width="720" />
</p>

<p align="center">
  <strong>Config-driven Unified LLM Gateway</strong>
</p>

<p align="center">
  Route, load-balance and manage Ollama, OpenAI and other LLM providers through a single authenticated API.
  Model Maestro gives you user-based access control, model mapping, token usage tracking, health-checked node pooling and a modern Next.js admin dashboard — all wired to PostgreSQL + Redis.
</p>

<p align="center">
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white" />
  </a>
  <a href="https://fastapi.tiangolo.com/">
    <img src="https://img.shields.io/badge/FastAPI-0.109.0-009688?logo=fastapi&logoColor=white" />
  </a>
  <a href="https://www.uvicorn.org/">
    <img src="https://img.shields.io/badge/Uvicorn-0.27.0-000000?logo=uvicorn&logoColor=white" />
  </a>
  <a href="https://www.postgresql.org/">
    <img src="https://img.shields.io/badge/PostgreSQL-15-336791?logo=postgresql&logoColor=white" />
  </a>
  <a href="https://redis.io/">
    <img src="https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white" />
  </a>
  <a href="https://nextjs.org/">
    <img src="https://img.shields.io/badge/Next.js-16.1.6-000000?logo=next.js&logoColor=white" />
  </a>
  <a href="https://react.dev/">
    <img src="https://img.shields.io/badge/React-19.2.3-61DAFB?logo=react&logoColor=black" />
  </a>
  <a href="https://tailwindcss.com/">
    <img src="https://img.shields.io/badge/Tailwind_CSS-v4-06B6D4?logo=tailwindcss&logoColor=white" />
  </a>
  <a href="https://www.docker.com/">
    <img src="https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white" />
  </a>
</p>

<p align="center">
  <a href="#quick-start"><strong>Quick Start</strong></a> ·
  <a href="#features"><strong>Features</strong></a> ·
  <a href="#architecture"><strong>Architecture</strong></a> ·
  <a href="#api-reference"><strong>API</strong></a> ·
  <a href="#admin-panel"><strong>Admin Panel</strong></a>
</p>

---

<!-- TOC -->

## Table of Contents

- [Quick Start](#quick-start)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Configuration](#configuration)
- [Admin Panel](#admin-panel)
- [API Reference](#api-reference)
  - [Authentication](#authentication)
  - [LLM Endpoints](#llm-endpoints)
  - [Admin Endpoints](#admin-endpoints)
  - [OpenAI Compatible](#openai-compatible)
- [Model Mapping & Routing](#model-mapping--routing)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License](#license)

<!-- /TOC -->

---

## Quick Start

> Requires Docker & Docker Compose.

```bash
# 1. Clone
git clone <repository-url> && cd model-maestro

# 2. Configure
cp .env.example .env

# 3. Launch full stack (PostgreSQL + Redis + FastAPI + Next.js)
docker compose -f docker-compose.dev.yml up --build -d

# 4. Seed the database
docker exec maestro python -m app.seeder

# 5. Open the admin panel at http://localhost:3000
```

| Service | URL | Notes |
|---|---|---|
| **API** | `http://localhost:8000` | FastAPI gateway |
| **Admin Dashboard** | `http://localhost:3000` | Next.js admin panel |
| **API Docs** | `http://localhost:8000/api/docs` | Basic-auth protected |

For a more detailed setup guide, see [`docs/SETUP.md`](docs/SETUP.md).

---

## Features

- **JWT Authentication** — Bearer-token auth on every LLM request.
- **Admin Dashboard** — Next.js 16 panel for visual management of users, nodes, models, groups and audit logs.
- **Model Mapping** — Translate display names (`gpt-oss:120b`) to real names (`gpt-oss:120b-cloud`) via PostgreSQL with JSON-file caching.
- **Multi-Node Load Balancing** — Round-robin, weighted and priority-based strategies across Ollama nodes.
- **Model Groups** — Group models into logical units with fallback chains. Requests dynamically resolve to the best member based on capability tags (vision, tools) and strategy.
- **Node Health Management** — Automatic health checks, model discovery and availability tracking.
- **User-Level Access Control** — Per-user model allowlists and rate limits (requests / tokens per day).
- **Token Usage Tracking** — Background-batched activity logs with prompt / completion / total token breakdowns.
- **Tool Set Filtering** — Restrict which tools a model is allowed to invoke via configurable tool sets.
- **Context Length Config** — Per-model context length stored in mappings (used by Cursor/Antigravity for usage bars).
- **Streaming** — SSE-based streaming on `/api/chat`, `/api/generate` and `/v1/chat/completions`.
- **OpenAI Compatible** — Drop-in `/v1/chat/completions` and `/v1/models` endpoints.
- **Full Ollama API** — `/api/generate`, `/api/chat`, `/api/embeddings`, `/api/tags`, `/api/show`, `/api/copy`, `/api/delete`, `/api/pull`, `/api/push`, `/api/create`.
- **Background Tasks** — Redis-backed async queue for activity logging, node health checks, model discovery and load cleanup.
- **Audit Logs** — Every admin action is timestamped and queryable.
- **PostgreSQL + Alembic** — Schema migrations run automatically on container startup.
- **Redis Cache** — Hot-path caching for mappings, config and user usage data.

---

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Cursor     │     │  Antigravity │     │   Claude     │
│   IDE        │     │   IDE        │     │   Code       │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       └────────────────────┼────────────────────┘
                            │
                     ┌──────┴──────┐
                     │  Load       │
                     │  Balancer   │
                     └──────┬──────┘
                            │
       ┌────────────────────┼────────────────────┐
       │                    │                    │
┌──────┴──────┐    ┌────────┴────────┐   ┌──────┴──────┐
│  Ollama     │    │    Ollama       │   │   OpenAI    │
│  Node 1     │    │    Node 2       │   │   / Other   │
└─────────────┘    └─────────────────┘   └─────────────┘
```

**Request Flow**

```
Client Request
      │
      ▼
┌─────────────────┐
│  JWT Middleware │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Model Group?    │──No──▶┌──────────────┐
│ (resolve member)│       │ Model Mapper │
└────────┬────────┘       │ (display→real)│
         │Yes             └──────┬───────┘
         │                        │
         ▼                        ▼
┌─────────────────┐       ┌──────────────┐
│ Load Balancer   │──────▶│ Node Pool    │
│ (pick healthy)  │       │ (health check│
└────────┬────────┘       │  + retry)    │
         │                └──────┬───────┘
         │                       │
         ▼                       ▼
┌─────────────────┐       ┌──────────────┐
│ Ollama Proxy    │◀──────│ Ollama /     │
│ (reverse map)   │       │ Provider API │
└────────┬────────┘       └──────────────┘
         │
         ▼
    Client Response
```

For the full architecture documentation, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Tech Stack

| Layer | Technology |
|---|---|
| **API Gateway** | Python 3.11, FastAPI, Uvicorn |
| **Async HTTP** | httpx (HTTP/2) |
| **Auth** | JWT (PyJWT) |
| **Database** | PostgreSQL 15 + asyncpg + SQLAlchemy async |
| **Migrations** | Alembic |
| **Cache** | Redis 7 |
| **Frontend** | Next.js 16, React 19, Tailwind CSS v4, shadcn/ui |
| **Background Tasks** | Redis-backed async queue |
| **Deployment** | Docker, Docker Compose |

---

## Configuration

Copy `.env.example` to `.env` and set:

```env
# Ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434
JWT_SECRET_KEY=change-this-to-a-strong-secret
LOG_LEVEL=INFO

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://maestro_user:maestro_password@postgres:5432/maestro

# Redis
REDIS_URL=redis://redis:6379/0

# Admin Token (for /admin/* endpoints)
ADMIN_TOKEN=change-this-for-production

# Admin Panel Login
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin

# Swagger / ReDoc Basic Auth
DOCS_USERNAME=admin
DOCS_PASSWORD=admin
```

---

## Admin Panel

The Next.js dashboard (`http://localhost:3000`) provides a visual interface for everything.

| Page | What you can do |
|---|---|
| **Dashboard** | Node health, model counts, user statistics |
| **Users** | Create users, manage tokens, assign models, set limits |
| **Ollama > Nodes** | Add/edit Ollama nodes, view health status, trigger discovery |
| **Ollama > Models** | Browse discovered models per node |
| **Models > Mappings** | Display↔Real name mappings, set context length, capabilities |
| **Models > Groups** | Create groups, add members, set strategy, reorder fallbacks |
| **Models > Config** | Per-model tool restrictions and settings |
| **Tool Sets** | Create tool groups and assign to models |
| **Settings** | System-wide configuration |
| **Audit Logs** | Filterable history of all admin actions |

**Default login:** username `admin`, password from `ADMIN_PASSWORD` in `.env`.

---

## API Reference

For the complete API reference with all request/response examples, see [`docs/API.md`](docs/API.md).

### Authentication

Every LLM request requires:

```
Authorization: Bearer <jwt-token>
```

Admin endpoints require:

```
Authorization: Bearer <admin-token>
```

### LLM Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/chat` | Chat completions (Ollama format) |
| `POST` | `/api/generate` | Text generation |
| `POST` | `/api/embeddings` | Generate embeddings |
| `GET`  | `/api/tags` | List available models |
| `POST` | `/api/show` | Show model info |
| `POST` | `/api/copy` | Copy model |
| `DELETE`| `/api/delete` | Delete model |
| `POST` | `/api/pull` | Pull model |
| `POST` | `/api/push` | Push model |
| `POST` | `/api/create` | Create model from Modelfile |

**Example — Chat**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss:120b",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

**Example — Streaming Chat**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss:120b",
    "messages": [{"role": "user", "content": "Tell me a story"}],
    "stream": true
  }'
```

### Admin Endpoints

**Users**

```bash
# Create user
curl -X POST http://localhost:8000/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "john"}'

# List users
curl http://localhost:8000/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Refresh token
curl -X PUT http://localhost:8000/admin/users/john/token \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Model Assignment**

```bash
# Assign specific models
curl -X POST http://localhost:8000/admin/users/john/models \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"models": ["gpt-oss:120b", "deepseek-v3.1:671b"]}'

# Grant access to all models
curl -X POST http://localhost:8000/admin/users/john/models/all \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**User Limits**

```bash
# Set limits (null = unlimited)
curl -X POST http://localhost:8000/admin/users/john/limits \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"request_limit": 1000, "token_limit": 1000000}'
```

**Model Mappings**

```bash
# Create mapping with context length
curl -X POST http://localhost:8000/admin/model-mappings \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "gpt-oss:120b",
    "real_name": "gpt-oss:120b-cloud",
    "context_length": 128000,
    "capabilities": ["completion", "tools"]
  }'

# List
curl http://localhost:8000/admin/model-mappings \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Delete
curl -X DELETE http://localhost:8000/admin/model-mappings/gpt-oss:120b \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Nodes**

```bash
# Add node
curl -X POST http://localhost:8000/admin/nodes \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "main", "base_url": "http://localhost:11434", "priority": 100}'

# Toggle activation
curl -X PATCH http://localhost:8000/admin/nodes/1/toggle \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

**Model Groups**

```bash
# Create group
curl -X POST http://localhost:8000/admin/model-groups \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "coding", "strategy": "round_robin", "description": "Code models"}'

# Add member
curl -X POST http://localhost:8000/admin/model-groups/coding/members \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model_display_name": "qwen3-coder:480b", "priority": 1}'
```

### OpenAI Compatible

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/v1/chat/completions` | Chat completions (OpenAI format) |
| `GET`  | `/v1/models` | Model list (OpenAI format) |

**Example — OpenAI Compatible**

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss:120b",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'
```

---

## Model Mapping & Routing

**Display Name → Real Name**

```
Client sends:       gpt-oss:120b
Proxy looks up:     gpt-oss:120b → gpt-oss:120b-cloud
Ollama receives:    gpt-oss:120b-cloud
```

**Real Name → Display Name**

```
Ollama returns:     gpt-oss:120b-cloud
Proxy translates:   gpt-oss:120b-cloud → gpt-oss:120b
Client sees:        gpt-oss:120b
```

**Model Groups**

If the requested model is a group, the gateway resolves it dynamically:

1. Detect if the request needs vision (image content in messages).
2. Filter members by capability tags (`vision`, `tools`).
3. Pick a member using the group's strategy:
   - `round_robin` — cycle through members
   - `weighted` — weighted random selection
   - `priority` — always pick lowest priority number
4. If the selected model fails, retry with the next member in priority order.

---

## Troubleshooting

**Restart the full stack**

```bash
docker compose -f docker-compose.dev.yml down
docker compose -f docker-compose.dev.yml up --build -d
```

**Run migrations manually**

```bash
docker exec maestro alembic upgrade head
```

**Re-run seeds**

```bash
docker exec maestro python -m app.seeder --reset
docker exec maestro python -m app.seeder
```

**Clear cache**

```bash
docker exec maestro python scripts/clear_cache.py
```

**Check PostgreSQL health**

```bash
docker exec maestro-postgres pg_isready -U maestro_user -d maestro
```

**Check Redis**

```bash
docker exec maestro-redis redis-cli ping
```

**View logs**

```bash
# All services
docker compose -f docker-compose.dev.yml logs -f

# API only
docker compose -f docker-compose.dev.yml logs -f maestro

# Frontend only
docker compose -f docker-compose.dev.yml logs -f frontend
```

---

## Development

### Project Structure

```
model-maestro/
├── app/
│   ├── main.py              # FastAPI app, routers, docs auth
│   ├── proxy.py             # Proxy logic, model routing, failover
│   ├── config.py            # Settings, ModelMappingManager, ModelGroupManager
│   ├── auth.py              # JWT authentication
│   ├── models.py            # Pydantic request/response models
│   ├── models_db.py         # SQLAlchemy ORM models
│   ├── database.py          # Async DB engine & session maker
│   ├── redis.py             # Redis client & queue
│   ├── load_balancer.py     # Node selection algorithms
│   ├── node_manager.py      # Health checks, discovery, node CRUD
│   ├── user_manager.py      # User CRUD
│   ├── background_tasks.py  # Activity log processor, health checks
│   ├── openclaw.py          # OpenClaw integration
│   ├── admin*.py            # Admin API routers
│   ├── repositories/        # Data access layer
│   ├── services/            # Business logic layer
│   └── seeds/               # DB seed migrations
├── frontend/
│   ├── src/app/             # Next.js App Router pages
│   ├── src/components/      # React components (sidebar, shell, etc.)
│   └── public/              # Static assets (logo, favicon)
├── docs/                    # Documentation (architecture, API, setup)
├── alembic/                 # Alembic migrations
├── tests/                   # pytest suite
├── docker-compose.dev.yml   # Dev stack (PG + Redis + API + Frontend)
├── docker-compose.yml       # Production stack (API + Frontend only)
└── Dockerfile               # FastAPI container
```

### Running Tests

```bash
python -m pytest tests/ -v
```

### Lint & Format

```bash
# Backend
python -m black app/
python -m ruff check app/

# Frontend
cd frontend && npm run lint
```

---

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — System architecture, request flow, database schema
- [`docs/API.md`](docs/API.md) — Complete API reference with all endpoints, requests and responses
- [`docs/SETUP.md`](docs/SETUP.md) — Detailed setup guide, environment variables, production deployment
- [`QUICKSTART.md`](QUICKSTART.md) — Get running in under 5 minutes

---

## License

MIT
