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
- [Antigravity (Google v1internal)](#antigravity-google-v1internal)
- [AWS Bedrock](#aws-bedrock)
- [Public Tunnel](#public-tunnel)
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
- **Antigravity Support** — Google v1internal API proxy via OAuth 2.0. Access Gemini and Claude models through Google's infrastructure as a first-class provider alongside Ollama and vLLM.
- **AWS Bedrock Support** — Native AWS Bedrock Converse API node type with automatic credential forwarding, image input and streaming support.
- **vLLM Support** — Native vLLM (OpenAI-compatible) node type with automatic health checks, model discovery and `Authorization: Bearer` header forwarding.
- **Public Tunnel** — One-click Cloudflare (quick or named tunnel) and ngrok integration to expose your local API publicly without manual setup.
- **Model Groups** — Group models into logical units with fallback chains. Requests dynamically resolve to the best member based on capability tags (vision, tools) and strategy.
- **Node Health Management** — Automatic health checks, model discovery and availability tracking for Ollama, vLLM, Antigravity and Bedrock nodes.
- **Per-Node Warmup Toggle** — Enable or disable model warmup per node via admin UI.
- **Drag-and-Drop Node Priority** — Reorder node cards in the admin panel to update fallback priority visually.
- **User-Level Access Control** — Per-user model/node/node-model allowlists and rate limits (requests / tokens per day). Restrict a user to specific nodes or even specific models on specific nodes.
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

# Google OAuth (for Antigravity nodes)
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=http://localhost:3000/admin/oauth/callback
```

---

## Antigravity (Google v1internal)

Model Maestro supports Google's v1internal API as a first-class provider alongside Ollama and vLLM. This gives you access to Gemini and Claude models through Google's infrastructure via OAuth 2.0.

### Setup

1. Add the official Antigravity OAuth credentials to `.env`:
   ```env
   GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-google-client-secret
   GOOGLE_REDIRECT_URI=http://localhost:3000/admin/oauth/callback
   ```
2. Restart the container: `docker compose restart maestro`
3. In the admin panel, create a node with **Node Type** = `antigravity`
4. Click **Google Auth** on the node detail page and sign in
5. Click **Sync Models** to fetch available models

### Usage

Force routing to Antigravity via node prefix:
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"model": "node:antigravity:gemini-3-flash", "messages": [{"role":"user","content":"Hello"}]}'
```

Or use model mappings to route transparently.

### Supported Features

- Chat completions, streaming, tool calls, image input
- Thinking models (gemini-3-pro, claude-opus-4-6-thinking)
- Automatic OAuth token refresh
- Endpoint fallback (Sandbox → Daily → Prod)

---

## AWS Bedrock

Model Maestro supports **AWS Bedrock** as a first-class node type. Bedrock nodes use the AWS Converse API behind an OpenAI-compatible facade, so existing clients work without changes.

### Setup

1. Create a node with **Node Type** = `bedrock`
2. Set **Base URL** to your AWS region endpoint, e.g. `https://bedrock-runtime.us-east-1.amazonaws.com`
3. Add AWS credentials in the node detail page:
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
4. Click **Sync Models** to fetch available foundation models

### Usage

Force routing to Bedrock via node prefix:
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"model": "node:bedrock:us.anthropic.claude-3-5-sonnet-20241022-v2:0", "messages": [{"role":"user","content":"Hello"}]}'
```

Or use model mappings to route transparently.

### Supported Features

- Chat completions and streaming via Bedrock Converse API
- Image input (base64)
- Tool calls (via Converse API toolConfig)
- Automatic AWS SigV4 signing

---

## Public Tunnel

Expose your local Model Maestro instance to the internet with one click — useful for testing IDE integrations or sharing temporary access.

### Supported Providers

| Provider | Mode | Requires Account | Notes |
|---|---|---|---|
| **Cloudflare** | Quick Tunnel | No | Random `*.trycloudflare.com` URL, auto-generated |
| **Cloudflare** | Named Tunnel | Yes | Custom domain via Cloudflare API + DNS |
| **ngrok** | — | Yes (recommended) | `pyngrok` Python package required |

### Setup

1. Go to **Settings** in the admin panel
2. Under **Tunnel**, select your provider:
   - **Cloudflare (Quick)**: leave `hostname` empty — a random URL is generated automatically
   - **Cloudflare (Named)**: fill `hostname` (e.g. `api.example.com`), `api_token`, `account_id` and optionally `zone_id`
   - **ngrok**: fill `api_token` with your ngrok auth token
3. Click **Start**
4. The public URL will appear in the **public_url** field once the tunnel is active

### API Endpoints

```bash
# Get tunnel status
curl http://localhost:8000/admin/tunnel/status \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Start tunnel (uses saved config)
curl -X POST http://localhost:8000/admin/tunnel/start \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Stop tunnel
curl -X POST http://localhost:8000/admin/tunnel/stop \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

---

## Admin Panel

The Next.js dashboard (`http://localhost:3000`) provides a visual interface for everything.

| Page | What you can do |
|---|---|
| **Dashboard** | Node health, model counts, user statistics |
| **Users** | Create users, manage tokens, assign models/nodes/node-models, set limits, activate/deactivate |
| **Nodes** | Add/edit Ollama, vLLM, Antigravity and Bedrock nodes, set codes, view health, trigger discovery, drag-and-drop priority |
| **AI Models > Models** | Tabbed view for Ollama, vLLM and Antigravity models with sync buttons, capabilities and context length |
| **AI Models > Mappings** | Display↔Real name mappings with provider badge (Ollama/vLLM/Antigravity), node-scoped overrides, context length, capabilities, sync caps |
| **AI Models > Groups** | Create groups, add members, set strategy, reorder fallbacks |
| **AI Models > Config** | Per-model tool restrictions and settings |
| **Tool Sets** | Create tool groups and assign to models |
| **Request Logs** | Filterable request history with source identification (Cursor, Claude, OpenClaw, Grafana, etc.) |
| **Settings** | System-wide configuration, tunnel setup |
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

**User Node & Node-Model Access**

Restrict which nodes and node-model combinations a user can access. If no restrictions are set, the user has access to everything.

```bash
# Grant access to specific nodes
curl -X POST http://localhost:8000/admin/users/john/nodes \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"node_ids": [1, 2]}'

# Grant access to specific models on a specific node
curl -X POST http://localhost:8000/admin/users/john/nodes/1/models \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"models": ["kimi-k2.6:latest"]}'

# Grant access to ALL nodes
curl -X POST http://localhost:8000/admin/users/john/nodes/all \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Grant access to ALL models on a node
curl -X POST http://localhost:8000/admin/users/john/nodes/1/models/all \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Revoke all node access (reset to unrestricted)
curl -X DELETE http://localhost:8000/admin/users/john/nodes \
  -H "Authorization: Bearer $ADMIN_TOKEN"
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

# Add Bedrock node (base_url auto-generated from region if omitted)
curl -X POST http://localhost:8000/admin/nodes \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "bedrock-us-east-1",
    "region": "us-east-1",
    "priority": 50,
    "code": "br",
    "node_type": "bedrock"
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

**Tunnel**

```bash
# Get tunnel status
curl http://localhost:8000/admin/tunnel/status \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Start tunnel with saved config
curl -X POST http://localhost:8000/admin/tunnel/start \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Stop tunnel
curl -X POST http://localhost:8000/admin/tunnel/stop \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Get tunnel config
curl http://localhost:8000/admin/tunnel/config \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Update tunnel config
curl -X PUT http://localhost:8000/admin/tunnel/config \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "cloudflare",
    "api_token": "your-cloudflare-api-token",
    "account_id": "your-account-id",
    "hostname": "api.example.com"
  }'
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
| `POST` | `/v1/chat/completions` | Chat completions (OpenAI format) |
| `POST` | `/v1/completions` | Text completions (OpenAI format) |
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
