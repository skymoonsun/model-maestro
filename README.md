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
    <img src="https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white" />
    <img src="https://img.shields.io/badge/FastAPI-0.109.0-009688?logo=fastapi&logoColor=white" />
    <img src="https://img.shields.io/badge/Uvicorn-0.27.0-000000?logo=uvicorn&logoColor=white" />
    <img src="https://img.shields.io/badge/PostgreSQL-15-336791?logo=postgresql&logoColor=white" />
    <img src="https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white" />
    <img src="https://img.shields.io/badge/Next.js-16.1.6-000000?logo=next.js&logoColor=white" />
    <img src="https://img.shields.io/badge/React-19.2.3-61DAFB?logo=react&logoColor=black" />
    <img src="https://img.shields.io/badge/Tailwind_CSS-v4-06B6D4?logo=tailwindcss&logoColor=white" />
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
- [IDE Integration](#ide-integration)
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
- **Node-Scoped Model Mappings** — Bind a mapping to a specific node so the same display name can resolve to different real names on different backends.
- **Node-Scoped Routing via Model Prefix** — Force a request to a specific node by prefixing the model name: `node:trmix:kimi-k2.6:latest` routes directly to the node with code `trmix`.
- **Multi-Node Load Balancing** — Round-robin, weighted and priority-based strategies across Ollama and vLLM nodes.
- **Antigravity (Google v1internal) Support** — Google AI Companion proxy via OAuth 2.0. Access Gemini models (gemini-3-flash, gemini-3.1-pro, claude-opus, etc.) through Google's internal v1internal API with full SSE streaming, tool calls, image support and automatic token refresh.
- **vLLM Support** — Native vLLM (OpenAI-compatible) node type with automatic health checks, model discovery and `Authorization: Bearer` header forwarding.
- **Model Groups** — Group models into logical units with fallback chains. Requests dynamically resolve to the best member based on capability tags (vision, tools) and strategy.
- **Node Health Management** — Automatic health checks, model discovery and availability tracking for both Ollama and vLLM nodes.
- **Per-Node Warmup Toggle** — Enable or disable model warmup per node via admin UI.
- **Drag-and-Drop Node Priority** — Reorder node cards in the admin panel to update fallback priority visually.
- **User-Level Access Control** — Per-user model allowlists and rate limits (requests / tokens per day).
- **Token Usage Tracking** — Background-batched activity logs with prompt / completion / total token breakdowns, plus request source identification (Cursor, Claude, OpenClaw, Grafana, etc.).
- **Tool Set Filtering** — Restrict which tools a model is allowed to invoke via configurable tool sets.
- **Unified Models Page** — Single tabbed view for both Ollama and vLLM models with live metadata (context length, capabilities, max model len) and one-click sync.
- **Sync Caps / Sync Meta** — Pull capabilities from Ollama (`/api/show`) and max_model_len from vLLM (`/v1/models`) directly from the admin UI.
- **Context Length Config** — Per-model context length stored in mappings (used by Cursor/Antigravity for usage bars).
- **Streaming** — SSE-based streaming on `/api/chat`, `/api/generate` and `/v1/chat/completions`.
- **OpenAI Compatible** — Drop-in `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings` and `/v1/models` endpoints.
- **Full Ollama API** — `/api/generate`, `/api/chat`, `/api/embeddings`, `/api/tags`, `/api/show`, `/api/copy`, `/api/delete`, `/api/pull`, `/api/push`, `/api/create`.
- **Grafana Assistant API** — Full Grafana LLM Assistant compatibility endpoints (`/grafana/assistant/*`) for Grafana-native AI features.
- **DeepSeek Tool Call Parsing** — Auto-detects and converts DeepSeek's raw XML tool call output (`<tool_calls><invoke>`, `<CallMcpTool>`, `<tool_call name="...">`) to OpenAI `tool_calls` format in streaming and non-streaming responses. Kimi/Moonshot `<|tool_calls_section_begin|>` format also supported.
- **Streaming-Aware Background Tasks** — Health checks, model discovery and warmup defer when streams are active, preventing interruptions.
- **Node-Aware Model Warmup** — Warmup requests target only models that exist on each node, eliminating 404 errors from stale model names.
- **Background Tasks** — Redis-backed async queue for activity logging, node health checks, model discovery, model warmup and load cleanup.
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

# Google OAuth (required for Antigravity nodes)
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=http://localhost:3000/admin/oauth/callback
```

---

## Antigravity (Google v1internal API Proxy)

Model Maestro can act as a proxy to Google's internal v1internal API (used by the Antigravity Manager), giving you access to Gemini and Claude models through Google's infrastructure — **as a first-class provider alongside Ollama and vLLM**.

### What is Antigravity?

Antigravity is a local proxy that connects to Google's internal v1internal API endpoints (`cloudcode-pa.googleapis.com`) via OAuth 2.0. Model Maestro implements the same protocol, letting you use Gemini models (e.g. `gemini-3-flash`, `gemini-3.1-pro`, `claude-opus-4`) through a standard OpenAI-compatible interface.

### Why use it as a provider?

By adding Antigravity as a **node type** in Model Maestro, you can:
- **Mix and match providers** — Route some requests to Ollama, some to vLLM, and some to Google's models from a single API.
- **Use node prefix routing** — Force a specific request to Google: `node:antigravity:gemini-3-flash`.
- **Apply the same access controls** — JWT auth, rate limits, model allowlists work identically.
- **Get unified logging** — All requests (Ollama, vLLM, Antigravity) appear in the same request logs.
- **Fallback between providers** — Put Google models in a model group with Ollama/vLLM fallbacks.

### Setup

1. **Get Google OAuth credentials** (choose one):
   - **Option A (recommended):** Use the official Antigravity Manager credentials from [lbjlaq/Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager) (`src-tauri/src/modules/oauth.rs`).
   - **Option B:** Create your own OAuth 2.0 client at [Google Cloud Console](https://console.cloud.google.com/apis/credentials) with redirect URI `http://localhost:3000/admin/oauth/callback` and enable the **Cloud Code Private API**.

2. **Add to `.env`:**

   ```env
   GOOGLE_CLIENT_ID=1071006060591-xxxxxxxx.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxx
   GOOGLE_REDIRECT_URI=http://localhost:3000/admin/oauth/callback
   ```

3. **Restart the container:**

   ```bash
   docker compose restart maestro
   ```

4. **Create an Antigravity node** in the admin panel:
   - Go to **Nodes** → **Add Node**
   - Set **Node Type** to `antigravity`
   - Give it a **Code** (e.g. `antigravity`) for prefix routing
   - Leave **Base URL** empty (v1internal endpoints are built-in)

5. **Connect Google Account:**
   - Open the node detail page
   - Click **Google Auth**
   - Sign in with your Google account and grant permissions
   - The OAuth flow completes automatically and stores the access/refresh tokens

6. **Sync Models:**
   - Click **Sync Models** on the node detail page
   - Available models (Gemini, Claude, etc.) are fetched from `fetchAvailableModels`

### Usage

Once connected, use Google models exactly like any other provider:

**Via node prefix (forces Antigravity):**

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "node:antigravity:gemini-3-flash",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

**Via model mapping (transparent):**

Create a mapping `gpt-4o → gemini-3.1-pro` in **AI Models > Mappings**, then:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

The proxy transparently routes to Antigravity if the resolved model belongs to the Antigravity node.

### Supported Features

| Feature | Status | Notes |
|---|---|---|
| **Chat Completions** | Full | OpenAI-compatible `/v1/chat/completions` |
| **Streaming** | Full | SSE with `alt=sse` |
| **Thinking / Extended Thinking** | Full | `gemini-3-pro`, `claude-opus-4-6-thinking` |
| **Tool Calls** | Full | Function calling with schema cleaning |
| **Image Input** | Full | Inline data and URL images |
| **System Prompts** | Full | Converted to `systemInstruction` |
| **Multi-turn** | Full | Alternating user/model roles |
| **Health Checks** | Full | Token validity + lightweight Google API check |
| **Token Refresh** | Automatic | Background refresh before expiry |
| **Model Discovery** | Full | Fetches from `fetchAvailableModels` |
| **Fallback** | Full | Sandbox → Daily → Prod endpoint fallback |

### Architecture

```
Client Request
      │
      ▼
┌─────────────────┐
│  Load Balancer  │── Antigravity node selected
└────────┬────────┘
         │
         ▼
┌─────────────────────────────┐
│  Model Maestro Antigravity  │
│  Proxy (google_proxy.py)  │
│                             │
│  OpenAI format ──▶ Google   │
│  v1internal format          │
│                             │
│  • OAuth token refresh      │
│  • Endpoint fallback        │
│  • SSE streaming            │
│  • Tool call transform      │
└────────┬────────────────────┘
         │
         ▼
┌─────────────────────────────┐
│  Google v1internal API      │
│  (cloudcode-pa.googleapis)  │
└─────────────────────────────┘
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `SERVICE_DISABLED` / 403 | Cloud Code Private API not enabled for your Google project | Use the official Antigravity Manager credentials (Option A) or enable the API in your Google Cloud Console |
| `Could not determine client ID` | `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` missing in `.env` | Add them and restart the container |
| Token refresh fails every hour | Refresh token revoked or expired | Re-authenticate via the node's **Google Auth** button |
| Models show `Available: No` | Discovery failed (token invalid) | Click **Sync Models** again or re-authenticate |
| 400 `max_tokens must be greater than thinking.budget_tokens` | Thinking model with low `max_tokens` | Model Maestro auto-adjusts `maxOutputTokens` for thinking models |

---

## Admin Panel

The Next.js dashboard (`http://localhost:3000`) provides a visual interface for everything.

| Page | What you can do |
|---|---|
| **Dashboard** | Node health, model counts, user statistics |
| **Users** | Create users, manage tokens, assign models, set limits |
| **Nodes** | Add/edit Ollama, vLLM and Antigravity nodes, set codes, view health, trigger discovery, drag-and-drop priority |
| **AI Models > Models** | Tabbed view for Ollama and vLLM models with sync buttons, capabilities and context length |
| **AI Models > Mappings** | Display↔Real name mappings with provider badge (Ollama/vLLM), node-scoped overrides, context length, capabilities, sync caps |
| **AI Models > Groups** | Create groups, add members, set strategy, reorder fallbacks |
| **AI Models > Config** | Per-model tool restrictions and settings |
| **Tool Sets** | Create tool groups and assign to models |
| **Request Logs** | Filterable request history with source identification (Cursor, Claude, OpenClaw, Grafana, etc.) |
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
| `POST` | `/v1/completions` | OpenAI-compatible completions |
| `POST` | `/v1/embeddings` | OpenAI-compatible embeddings |
| `GET`  | `/res/v1/web/search` | Brave Search-compatible web search |

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
# Add node (with optional code for prefix routing)
curl -X POST http://localhost:8000/admin/nodes \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main",
    "base_url": "http://localhost:11434",
    "priority": 100,
    "code": "trmix",
    "node_type": "ollama"
  }'

# Toggle activation
curl -X PATCH http://localhost:8000/admin/nodes/1/toggle \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Reorder node priorities (drag-and-drop)
curl -X PATCH http://localhost:8000/admin/nodes/batch/priority \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"priorities": [{"id": 1, "priority": 200}, {"id": 2, "priority": 100}]}'
```

**Antigravity (Google OAuth)**

```bash
# Get OAuth authorization URL
curl http://localhost:8000/admin/nodes/1/google-auth-url \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Handle OAuth callback (POST from frontend)
curl -X POST http://localhost:8000/admin/nodes/1/google-auth-callback \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"code": "4/abc...", "state": "1:xyz..."}'

# Refresh OAuth token manually
curl -X POST http://localhost:8000/admin/nodes/1/google-refresh-token \
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

**Grafana Assistant**

```bash
# List chats
curl http://localhost:8000/grafana/assistant/chats \
  -H "Authorization: Bearer $TOKEN"

# Create chat
curl -X POST http://localhost:8000/grafana/assistant/chats \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello"}'

# Stream chat
curl -X POST http://localhost:8000/grafana/assistant/chat/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello"}'

# Get LLM config
curl http://localhost:8000/grafana/assistant/config \
  -H "Authorization: Bearer $TOKEN"

# Update LLM config
curl -X POST http://localhost:8000/grafana/assistant/config \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-oss:120b", "temperature": 0.7}'

# Check infrastructure discovery status
curl http://localhost:8000/grafana/assistant/discovery \
  -H "Authorization: Bearer $TOKEN"
```

### OpenAI Compatible

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/v1/chat/completions` | Chat completions (OpenAI format) — supports Ollama, vLLM, Antigravity |
| `POST` | `/v1/completions` | Text completions (OpenAI format) — supports Ollama, vLLM, Antigravity |
| `POST` | `/v1/embeddings` | Embeddings (OpenAI format) |
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

**Node Prefix Routing**

Force a request to a specific node by prefixing the model name with its `code`:

```
Client sends:       node:trmix:kimi-k2.6:latest
Gateway parses:     code = "trmix", model = "kimi-k2.6:latest"
Node lookup:        trmix → node #3
Model mapping:      kimi-k2.6:latest → kimi-k2.6:latest-cloud
Node #3 receives:   kimi-k2.6:latest-cloud
```

**Antigravity routing example:**

```
Client sends:       node:antigravity:gemini-3-flash
Gateway parses:     code = "antigravity", model = "gemini-3-flash"
Node lookup:        antigravity → node #5
Proxy transforms:   OpenAI format → Google v1internal format
Google receives:      model=gemini-3-flash, project=your-project-id
```

- Syntax: `node:{code}:{model_name}`
- The `code` is the unique short identifier set on each node in the admin panel.
- If the code does not exist, the gateway returns `404 Node with code 'x' not found`.
- When a prefix is present, the load balancer is skipped and the request goes directly to the matched node.
- Prefix routing works on every endpoint that accepts a `model` parameter: `/api/chat`, `/api/generate`, `/v1/chat/completions`, `/v1/embeddings`, etc.

**Model Groups**

If the requested model is a group, the gateway resolves it dynamically:

1. Detect if the request needs vision (image content in messages).
2. Filter members by capability tags (`vision`, `tools`).
3. Pick a member using the group's strategy:
   - `round_robin` — cycle through members
   - `weighted` — weighted random selection
   - `priority` — always pick lowest priority number
4. If the selected model fails, retry with the next member in priority order.

**Node-Scoped Mappings**

A model mapping can be bound to a specific node so the same display name resolves to a different real name on different backends. This is useful when nodes host different variants of the same model (e.g. a CPU-quantized version on one node and a full-GPU version on another).

---

## IDE Integration

Model Maestro is designed to be the backend for modern AI-powered IDEs and tools. See the full integration guide for step-by-step setup:

- **[Claude Code](docs/IDE_INTEGRATION.md#claude-code)** — `ANTHROPIC_BASE_URL` override
- **[OpenClaw](docs/IDE_INTEGRATION.md#openclaw)** — `openclaw.json` provider configuration
- **[Cursor](docs/IDE_INTEGRATION.md#cursor)** — OpenAI API Key + custom base URL
- **[Grafana Assistant](docs/IDE_INTEGRATION.md#grafana-assistant)** — Grafana plugin with domain bypass script or reverse proxy

For complete configuration examples and troubleshooting, see [`docs/IDE_INTEGRATION.md`](docs/IDE_INTEGRATION.md).

## Web Search Integration (Brave Search)

Model Maestro provides a **Brave Search-compatible endpoint** (`/res/v1/web/search`) that can be used by OpenClaw and other clients expecting Brave Search semantics.

- **Authentication**: `X-Subscription-Token` or `Authorization: Bearer <token>`
- **Backend**: Forwards to Ollama Web Search (or any custom search proxy)
- **Response Format**: Full Brave Search API compatibility

### Quick Setup (OpenClaw)

Add to `~/.openclaw/openclaw.json`:

```json
{
  "plugins": {
    "entries": {
      "brave": {
        "enabled": true,
        "config": {
          "webSearch": {
            "apiKey": "<maestro-jwt-token>"
          }
        }
      }
    }
  }
}
```

Use the [patcher script](docs/OPENCLAW_BRAVE_SEARCH.md#3-brave-url-patcher-script) to automatically redirect OpenClaw's Brave URL to your Maestro instance.

For the complete setup guide (including cron configuration, manual testing, and backend proxy options), see [`docs/OPENCLAW_BRAVE_SEARCH.md`](docs/OPENCLAW_BRAVE_SEARCH.md).

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

**NVIDIA API (`integrate.api.nvidia.com`) — Known Limitations**

NVIDIA's hosted NIM endpoints are not fully OpenAI-compatible and impose strict request validation:

| Parameter | Status | Notes |
|---|---|---|
| `tools` | **Unsupported** | NVIDIA backend crashes with `unhashable type: 'dict'` (CherryHQ/cherry-studio#14868). Proxy strips this automatically for NVIDIA endpoints. |
| `tool_choice` | **Unsupported** | Stripped automatically. |
| `stream_options` | **Unsupported** | Stripped automatically. |
| `presence_penalty` | **Unsupported** | Returns 422. Stripped automatically. |
| `frequency_penalty` | **Unsupported** | Returns 422. Stripped automatically. |
| `max_tokens` | **Avoid** | Injection causes 500 errors on some models (e.g. Kimi K2.6). Proxy skips injection for NVIDIA URLs. |
| `system` role | **Avoid** | May be rejected; use `user` role only. |
| Message order | **Strict** | Must alternate `user`/`assistant`. System message can only appear at the start. |

> **Workaround:** If you need tool support with Kimi K2.6 or other NVIDIA-hosted models, run them on a self-hosted vLLM node instead. Generic vLLM nodes support tools, tool_choice and all standard parameters without restriction.

---

## Development

### Project Structure

```
model-maestro/
├── app/
│   ├── main.py              # FastAPI app, routers, docs auth
│   ├── proxy.py             # Proxy logic, model routing, failover, tool call parsing
│   ├── config.py            # Settings, ModelMappingManager, ModelGroupManager
│   ├── auth.py              # JWT authentication
│   ├── models.py            # Pydantic request/response models
│   ├── models_db.py         # SQLAlchemy ORM models
│   ├── database.py          # Async DB engine & session maker
│   ├── redis.py             # Redis client & queue
│   ├── load_balancer.py     # Node selection algorithms
│   ├── node_manager.py      # Health checks, discovery, node CRUD
│   ├── user_manager.py      # User CRUD
│   ├── background_tasks.py  # Activity log processor, health checks, model warmup
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
- [`docs/IDE_INTEGRATION.md`](docs/IDE_INTEGRATION.md) — Claude Code, OpenClaw, Cursor and Grafana Assistant setup
- [`QUICKSTART.md`](QUICKSTART.md) — Get running in under 5 minutes

---

## License

MIT
