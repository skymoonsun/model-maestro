# Architecture

This document describes the internal architecture of Model Maestro.

---

## Overview

Model Maestro acts as a unified gateway between LLM clients (IDEs, CLI tools, applications) and multiple LLM backends (Ollama nodes, OpenAI, other providers). It handles authentication, request routing, load balancing, model name translation, usage tracking and admin operations.

---

## Request Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                              CLIENT                                  │
│   Cursor IDE / Antigravity / Claude Code / CLI / Custom App         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         MODEL MAESTRO                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │ JWT Auth     │  │ Model Group  │  │ Model Mapper │  │ Load      │ │
│  │ Middleware   │─▶│ Resolver     │─▶│ (display↔real│─▶│ Balancer  │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────┬─────┘ │
│                                                               │       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │       │
│  │ Background   │  │ Node Manager │  │ Proxy Engine │◀───────┘       │
│  │ Tasks        │  │ (health/discovery)│ (httpx)   │                │
│  └──────────────┘  └──────────────┘  └──────────────┘                │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ PostgreSQL   │  │ Redis        │  │ JSON Cache   │              │
│  │ (users,      │  │ (queue,      │  │ (mappings,   │              │
│  │  models,     │  │  sessions)   │  │  configs)    │              │
│  │  audit logs) │  │              │  │              │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│ Ollama Node 1 │    │ vLLM Node 2   │    │ Other Provider│
│ (primary)     │    │ (OpenAI fmt)  │    │ (OpenAI, etc) │
└───────────────┘    └───────────────┘    └───────────────┘
```

---

## Component Breakdown

### 1. JWT Authentication (`app/auth.py`)

Every LLM request must include a valid JWT token in the `Authorization: Bearer <token>` header. Tokens are signed with `JWT_SECRET_KEY` and encode the username. Admin endpoints use a separate `ADMIN_TOKEN`.

### 2. Model Group Resolver (`app/config.py` → `ModelGroupManager`)

If the requested model name matches a group, the gateway resolves it to a specific member model based on:
- **Capability detection**: Checks if the request contains images (vision capability needed).
- **Strategy**: `round_robin`, `weighted`, or `priority`.
- **Fallback**: If the selected member fails, the next member in priority order is tried.

### 3. Model Mapper (`app/config.py` → `ModelMappingManager`)

Translates between display names (what the client sees) and real names (what Ollama receives). Mappings are stored in PostgreSQL and cached to a JSON file on disk for fast lookups without DB hits.

**Node-scoped mappings**: A mapping can be bound to a specific `node_id`. When a request is routed to that node, the node-scoped mapping takes precedence over global mappings. This allows the same display name to resolve to different real names on different backends.

### 4. Load Balancer (`app/load_balancer.py`)

Selects which Ollama node to send the request to. Nodes are filtered by health status, then selected by:
- Priority (higher = preferred)
- Weight (higher = more traffic)
- Active request count (least-loaded)

**Node prefix routing**: If the model name starts with `node:{code}:{model}`, the load balancer is skipped entirely and the request is routed directly to the node matching `code`.

### 5. Proxy Engine (`app/proxy.py`)

Uses `httpx` (with HTTP/2 support) to forward requests to the selected Ollama node. Handles:
- Request body rewriting (model name translation)
- Response body rewriting (reverse model name translation)
- Streaming (SSE) passthrough
- Tool call validation and sanitization (Cursor compatibility)
- **DeepSeek tool call parsing**: Auto-detects and converts DeepSeek XML tool call output to OpenAI `tool_calls` format
- **Request source detection**: Identifies the client (Cursor, Claude, OpenClaw, Grafana, Ollama Native, OpenAI-Compatible) for usage analytics
- **Node prefix routing**: Parses `node:{code}:{model}` syntax to force direct node selection
- **vLLM proxying**: Forwards `Authorization: Bearer` headers to vLLM nodes and handles OpenAI-compatible responses
- Failover retries across nodes and model group members

### 6. Node Manager (`app/node_manager.py`)

Manages Ollama and vLLM nodes:
- **Health checks**: Periodic HTTP health checks to each node (`/api/tags` for Ollama, `/v1/models` for vLLM).
- **Model discovery**: Periodically fetches available models from each node to populate the `node_models` table.
- **Activation toggling**: Nodes can be activated/deactivated via admin API.
- **Node type discrimination**: Nodes are tagged as `ollama` or `vllm`. vLLM nodes use OpenAI-compatible health checks and forward `Authorization: Bearer` headers.
- **Warmup toggle**: Per-node `warmup_enabled` flag controls whether model warmup requests are sent to that node.

### 7. Background Tasks (`app/background_tasks.py`)

Redis-backed async task system:
- **Activity log processor**: Batches user activity logs from Redis queue to PostgreSQL.
- **Node health checker**: Runs periodic health checks.
- **Model discovery**: Periodically refreshes model lists from nodes.
- **Load cleanup**: Resets per-node load metrics.

### 8. Admin API (`app/admin*.py`)

RESTful endpoints for managing users, model mappings, nodes, groups, tool sets, system config and audit logs. All protected by `ADMIN_TOKEN`.

### 9. Frontend (`frontend/`)

Next.js 16 + React 19 + Tailwind CSS v4 + shadcn/ui dashboard. Communicates with the FastAPI backend via REST. Features:
- JWT-based session management
- Server-side data fetching with React Query
- Drag-and-drop model group member reordering
- Real-time node health indicators

---

## Data Flow: Model Mapping

```
Client Request
    model: "gpt-oss:120b"
         │
         ▼
┌─────────────────┐
│ Node Prefix     │
│ Parser          │
└────────┬────────┘
         │ No prefix
         ▼
┌─────────────────┐
│ Model Group     │
│ Resolver        │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Model Mapper    │
│ (PostgreSQL +   │
│  JSON cache)    │
│  node-scoped    │
└────────┬────────┘
         │
         ▼
    model: "gpt-oss:120b-cloud"
         │
         ▼
┌─────────────────┐
│ Load Balancer   │
│ or Direct Node  │
│ (if prefix)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Proxy Engine    │
│ (httpx → Node)  │
└────────┬────────┘
         │
         ▼
    Ollama / vLLM Node
         │
         ▼
    Response (model: "gpt-oss:120b-cloud")
         │
         ▼
┌─────────────────┐
│ Reverse Mapper  │
└────────┬────────┘
         │
         ▼
    Client Response (model: "gpt-oss:120b")
```

---

## Data Flow: User Activity Tracking

```
LLM Request
    │
    ▼
Proxy Engine
    │
    ├──▶ Redis Queue (activity_log_queue)
    │
    ▼
Background Task Processor
    │
    ├──▶ Batch 50 logs at a time
    │
    ▼
PostgreSQL (user_activity_logs table)
```

---

## Database Schema (Simplified)

```
users
├── username (PK)
├── token
├── is_active
├── has_all_models
├── created_at
└── updated_at

user_models
├── username (FK)
├── model_name
└── created_at

user_limits
├── username (FK)
├── request_limit
├── token_limit
└── updated_at

model_mappings
├── display_name (PK)
├── real_name
├── context_length
├── capabilities (JSONB)
├── node_id (FK, nullable)
└── created_at

model_groups
├── name (PK)
├── strategy
├── description
└── created_at

model_group_members
├── id (PK)
├── group_name (FK)
├── model_display_name
├── priority
├── weight
├── is_active
├── capability_tags (JSONB)
└── created_at

ollama_nodes
├── id (PK)
├── name
├── base_url
├── api_key
├── node_type (ollama | vllm)
├── code (unique, nullable)
├── priority
├── weight
├── is_active
├── warmup_enabled
├── health_status
├── last_health_check
└── created_at

node_models
├── id (PK)
├── node_id (FK)
├── model_name
├── model_family
├── model_size
├── model_digest
├── model_capabilities (JSONB)
├── is_available
├── last_seen
└── created_at

node_load_metrics
├── id (PK)
├── node_id (FK)
├── active_requests
├── total_requests_today
├── last_5_min_requests
├── avg_response_time_ms
├── cpu_usage
├── memory_usage
└── recorded_at

user_activity_logs
├── id (PK)
├── username
├── model_name
├── request_type
├── prompt_tokens
├── completion_tokens
├── total_tokens
├── source (Cursor, Claude, OpenClaw, Grafana, etc.)
├── url_path
└── created_at

audit_logs
├── id (PK)
├── action
├── entity_type
├── entity_id
├── performed_by
├── details (JSONB)
└── created_at
```

---

## Caching Strategy

| Cache Layer | What | TTL / Behavior |
|---|---|---|
| **JSON File** (`/app/cache/model_mappings.json`) | Model mappings, context lengths, capabilities | Persistent, reloaded on startup |
| **Redis** | User daily usage, session data, queue | Configurable TTL |
| **In-Memory** | Model groups, node pool state | Lives with process, reloaded on startup |

---

## Security Model

1. **JWT for LLM requests**: Every `/api/*` and `/v1/*` request must carry a valid user JWT.
2. **Admin Token for management**: Every `/admin/*` request must carry the `ADMIN_TOKEN`.
3. **Basic Auth for docs**: Swagger UI and ReDoc are protected by `DOCS_USERNAME`/`DOCS_PASSWORD`.
4. **Model Access Control**: Users can only access models explicitly assigned to them (or all models if `has_all_models=true`).
5. **Rate Limiting**: Per-user daily request and token limits can be configured.
6. **vLLM Auth Forwarding**: For vLLM nodes, the proxy forwards `Authorization: Bearer <api_key>` headers using the node's stored `api_key`.

---

For the API reference, see [`API.md`](API.md).
