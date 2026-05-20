# API Reference

Complete reference for all Model Maestro API endpoints.

---

## Table of Contents

- [Authentication](#authentication)
- [LLM Endpoints](#llm-endpoints)
  - [Chat](#chat)
  - [Generate](#generate)
  - [Embeddings](#embeddings)
  - [Tags](#tags)
  - [Show](#show)
  - [Copy](#copy)
  - [Delete](#delete)
  - [Pull](#pull)
  - [Push](#push)
  - [Create](#create)
- [OpenAI Compatible](#openai-compatible)
  - [Chat Completions](#chat-completions)
  - [Completions](#completions)
  - [Embeddings](#embeddings)
  - [Models](#models)
- [Claude API (Anthropic-compatible)](#claude-api-anthropic-compatible)
  - [List Models](#list-models-claude)
  - [Messages](#messages)
  - [Count Tokens](#count-tokens)
  - [Claude Desktop header](#claude-desktop-header)
- [Grafana Assistant](#grafana-assistant)
  - [Chats](#chats)
  - [Chat Stream](#chat-stream)
  - [Config](#config)
  - [Discovery](#discovery)
- [Admin Endpoints](#admin-endpoints)
  - [Users](#users)
  - [Model Assignments](#model-assignments)
  - [User Limits](#user-limits)
  - [Activity Logs](#activity-logs)
  - [Token Usage](#token-usage)
  - [Model Usage](#model-usage)
  - [Model Mappings](#model-mappings)
  - [Nodes](#nodes)
  - [Model Groups](#model-groups)
  - [System Config](#system-config)
  - [Tool Sets](#tool-sets)
  - [Audit Logs](#audit-logs)

---

## Authentication

### LLM Requests

```
Authorization: Bearer <jwt-token>
```

### Admin Requests

```
Authorization: Bearer <admin-token>
```

---

## LLM Endpoints

All LLM endpoints require a valid JWT token and enforce model access control.

### Chat

```bash
POST /api/chat
```

**Request body:**

```json
{
  "model": "gpt-oss:120b",
  "messages": [
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false,
  "options": null,
  "template": null,
  "format": null,
  "keep_alive": null
}
```

**Response:**

```json
{
  "model": "gpt-oss:120b",
  "message": {
    "role": "assistant",
    "content": "Hello! How can I help you today?"
  },
  "done": true
}
```

**Streaming:** Set `"stream": true`. Response is SSE with `data:` lines.

---

### Generate

```bash
POST /api/generate
```

**Request body:**

```json
{
  "model": "deepseek-v3.1:671b",
  "prompt": "Write a Python function to reverse a string",
  "stream": false,
  "options": null,
  "context": null,
  "template": null,
  "system": null,
  "raw": false
}
```

**Response:**

```json
{
  "model": "deepseek-v3.1:671b",
  "response": "def reverse_string(s):\n    return s[::-1]",
  "done": true
}
```

---

### Embeddings

```bash
POST /api/embeddings
```

**Request body:**

```json
{
  "model": "bge-m3:latest",
  "prompt": "Hello world"
}
```

**Response:**

```json
{
  "embedding": [0.0123, -0.0456, ...]
}
```

---

### Tags

```bash
GET /api/tags
```

**Response:**

```json
{
  "models": [
    {
      "name": "gpt-oss:120b",
      "model": "gpt-oss:120b",
      "size": 77337069136,
      "digest": "abc123...",
      "details": { ... }
    }
  ]
}
```

Lists only models the user is allowed to access. Display names are returned (cloud suffix stripped).

---

### Show

```bash
POST /api/show
```

**Request body:**

```json
{
  "model": "gpt-oss:120b",
  "verbose": false
}
```

---

### Copy

```bash
POST /api/copy
```

**Request body:**

```json
{
  "source": "gpt-oss:120b",
  "destination": "gpt-oss:120b-backup"
}
```

---

### Delete

```bash
DELETE /api/delete
```

**Request body:**

```json
{
  "model": "gpt-oss:120b"
}
```

---

### Pull

```bash
POST /api/pull
```

**Request body:**

```json
{
  "model": "llama3:latest",
  "stream": false
}
```

---

### Push

```bash
POST /api/push
```

**Request body:**

```json
{
  "model": "username/model:tag"
}
```

---

### Create

```bash
POST /api/create
```

**Request body:**

```json
{
  "model": "my-custom-model",
  "modelfile": "FROM llama3\nSYSTEM You are a helpful assistant."
}
```

---

## OpenAI Compatible

### Chat Completions

```bash
POST /v1/chat/completions
```

**Request body:**

```json
{
  "model": "gpt-oss:120b",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 2048
}
```

**Response:**

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1712345678,
  "model": "gpt-oss:120b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 15,
    "completion_tokens": 20,
    "total_tokens": 35
  }
}
```

**Streaming:** Set `"stream": true`. Returns SSE with `data:` lines.

---

### Completions

```bash
POST /v1/completions
```

**Request body:**

```json
{
  "model": "gpt-oss:120b",
  "prompt": "Once upon a time",
  "max_tokens": 100,
  "temperature": 0.7,
  "stream": false
}
```

**Response:**

```json
{
  "id": "cmpl-...",
  "object": "text_completion",
  "created": 1712345678,
  "model": "gpt-oss:120b",
  "choices": [
    {
      "text": " there was a developer who loved LLMs.",
      "index": 0,
      "logprobs": null,
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 5,
    "completion_tokens": 10,
    "total_tokens": 15
  }
}
```

---

### Embeddings

```bash
POST /v1/embeddings
```

**Request body:**

```json
{
  "model": "bge-m3:latest",
  "input": "Hello world",
  "encoding_format": "float"
}
```

**Response:**

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.0123, -0.0456, ...],
      "index": 0
    }
  ],
  "model": "bge-m3:latest",
  "usage": {
    "prompt_tokens": 2,
    "total_tokens": 2
  }
}
```

---

### Models

```bash
GET /v1/models
```

**Response:**

```json
{
  "object": "list",
  "data": [
    {
      "id": "gpt-oss:120b",
      "object": "model",
      "created": 1712345678,
      "owned_by": "model-maestro"
    }
  ]
}
```

---

## Claude API (Anthropic-compatible)

Used by **Claude Code**, **Claude Desktop (Cowork 3P gateway)**, and VS Code Claude Code extension.

**Base path:** `/claude/` (e.g. `https://maestro.example.com/claude/v1/messages`)

**Authentication:** Same JWT as other LLM endpoints (`Authorization: Bearer <token>`).

See also: [IDE Integration Guide](IDE_INTEGRATION.md) for client-specific setup (Desktop header, model discovery, env vars).

### List Models (Claude)

```bash
GET /claude/v1/models
```

Returns Anthropic-style model objects (`id`, `display_name`, `capabilities`, `max_input_tokens`, …) for models the user is allowed to use.

**Claude Desktop only:** When the client sends `X-Maestro-Client: claude-desktop` (see below), Maestro:

- Assigns **opaque** `id` values: `claude-maestro-{hash}` (12 hex chars from SHA-256 of the routing name)
- Keeps **`display_name`** as the real catalog name (e.g. `google/codegemma-7b`, `kimi-k2.6:latest`)
- Stores hash → routing name in Redis (`maestro:claude_desktop_route:{hash}`)

Without the Desktop header, list entries use the legacy `claude-{name}` id format (Claude Code).

**Example (Desktop client):**

```json
{
  "data": [
    {
      "type": "model",
      "id": "claude-maestro-2bf4c98a7478",
      "display_name": "kimi-k2.6:latest",
      "max_input_tokens": 131072,
      "capabilities": { "...": "..." }
    }
  ],
  "has_more": false
}
```

---

### Messages

```bash
POST /claude/v1/messages
```

Anthropic Messages API compatible body (`model`, `messages`, `max_tokens`, `stream`, `tools`, `system`, …).

**Model field:**

| Client | `model` value |
|--------|----------------|
| Claude Code | Maestro mapping / catalog name, often with `claude-` prefix stripped server-side |
| Claude Desktop | Opaque id from discovery: `claude-maestro-{hash}` |

**Routing:** Maestro resolves the model name, applies model groups and node load balancing, then proxies to Ollama / vLLM / Antigravity / Bedrock as configured.

**Streaming:** Controlled by system config `claude.streaming_enabled` (default may force non-streaming).

---

### Count Tokens

```bash
POST /claude/v1/messages/count_tokens
```

Same `model` rules as [Messages](#messages). Returns `{"input_tokens": <int>}`.

---

### Claude Desktop header

Claude Desktop (Cowork **third-party inference**, `inferenceProvider: gateway`) must send a custom header on **every** inference call (model discovery, test connection, chat):

| Header | Value |
|--------|--------|
| `X-Maestro-Client` | `claude-desktop` |

Also accepted: `cowork`, `desktop`.

Configure in Desktop: **Developer → Configure third-party inference → Custom inference headers** (`inferenceCustomHeaders`):

```json
{
  "X-Maestro-Client": "claude-desktop"
}
```

**Behavior:**

| Request | Header present | Result |
|---------|----------------|--------|
| `GET /claude/v1/models` | Yes | Opaque ids + Desktop-friendly `capabilities` |
| `GET /claude/v1/models` | No | Standard `claude-{name}` ids (Claude Code style) |
| `POST /claude/v1/messages` with `claude-maestro-…` | Yes | Hash resolved → real routing name |
| `POST /claude/v1/messages` with `claude-maestro-…` | No | **404** `Model 'claude-maestro-…' not found` (no hash lookup) |

**Why opaque ids:** Claude Desktop 1.6259+ rejects model ids containing substrings such as `kimi`, `qwen`, `gemma`, `deepseek`, etc., even when the id starts with `claude-`. Opaque ids avoid exposing those strings in the `id` field while `display_name` stays human-readable.

**Gateway URL (Desktop):**

| Field | Value |
|-------|--------|
| Inference provider | `gateway` |
| Gateway base URL | `https://maestro.example.com/claude` |
| Gateway API key | Maestro JWT |
| Custom headers | `{"X-Maestro-Client": "claude-desktop"}` |

After deploy or catalog changes, re-run **Test model discovery** in Desktop so it picks up new `claude-maestro-…` ids.

---

## Admin Endpoints

All admin endpoints require the `ADMIN_TOKEN` in the `Authorization: Bearer <token>` header.

### Users

#### Create User

```bash
POST /admin/users
```

**Body:** `{"username": "john"}`

**Response:**

```json
{
  "username": "john",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "created_at": "2024-01-20T10:30:00",
  "updated_at": null,
  "is_active": true
}
```

#### List Users

```bash
GET /admin/users
```

**Response:**

```json
[
  {
    "username": "john",
    "token": "eyJ...",
    "created_at": "2024-01-20T10:30:00",
    "updated_at": null,
    "is_active": true,
    "has_all_models": false,
    "models": ["gpt-oss:120b"]
  }
]
```

#### Get User

```bash
GET /admin/users/{username}
```

#### Refresh Token

```bash
PUT /admin/users/{username}/token
```

**Response:** Returns user with a new JWT token.

#### Delete User

```bash
DELETE /admin/users/{username}
```

---

### Model Assignments

#### Assign Specific Models

```bash
POST /admin/users/{username}/models
```

**Body:** `{"models": ["gpt-oss:120b", "deepseek-v3.1:671b"]}`

#### Grant All Models

```bash
POST /admin/users/{username}/models/all
```

#### Get User's Models

```bash
GET /admin/users/{username}/models
```

#### Remove Specific Model

```bash
DELETE /admin/users/{username}/models/{model_name}
```

#### Remove All Models

```bash
DELETE /admin/users/{username}/models/all
```

---

### User Limits

#### Set Limits

```bash
POST /admin/users/{username}/limits
```

**Body:**

```json
{
  "request_limit": 1000,
  "token_limit": 1000000
}
```

Use `null` for unlimited.

#### Get Limits

```bash
GET /admin/users/{username}/limits
```

#### Delete Limits

```bash
DELETE /admin/users/{username}/limits
```

---

### Activity Logs

#### Get User Activity

```bash
GET /admin/users/{username}/activity?limit=50&offset=0
```

**Response:**

```json
{
  "username": "john",
  "activities": [
    {
      "model_name": "gpt-oss:120b",
      "request_type": "chat",
      "prompt_tokens": 150,
      "completion_tokens": 300,
      "total_tokens": 450,
      "source": "Cursor",
      "url_path": "/api/chat",
      "created_at": "2024-01-20T10:30:00"
    }
  ],
  "total_returned": 1,
  "limit": 50,
  "offset": 0
}
```

---

### Request Logs

#### Get System-Wide Request Logs

```bash
GET /admin/dashboard/requests-log?limit=50&offset=0&source=Cursor&url_path=/api/chat
```

**Query params:**
- `limit` — default 50
- `offset` — default 0
- `source` — filter by request source (`Cursor`, `Claude`, `OpenClaw`, `Grafana`, `Ollama Native`, `OpenAI-Compatible`, `Unknown`)
- `url_path` — filter by URL path

**Response:**

```json
{
  "requests": [
    {
      "id": 1,
      "username": "john",
      "model_name": "gpt-oss:120b",
      "request_type": "chat",
      "prompt_tokens": 150,
      "completion_tokens": 300,
      "total_tokens": 450,
      "source": "Cursor",
      "url_path": "/api/chat",
      "created_at": "2024-01-20T10:30:00"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

---

### Token Usage

#### Get Token Usage Stats

```bash
GET /admin/users/{username}/token-usage?start_date=2024-01-01&end_date=2024-01-31
```

**Response:**

```json
{
  "username": "john",
  "usage": {
    "prompt_tokens": 5000,
    "completion_tokens": 10000,
    "total_tokens": 15000,
    "total_requests": 25
  },
  "period": {
    "start_date": "2024-01-01",
    "end_date": "2024-01-31"
  }
}
```

---

### Model Usage

#### Get Model Usage Stats

```bash
GET /admin/users/{username}/model-usage?start_date=2024-01-01&end_date=2024-01-31
```

**Response:**

```json
{
  "username": "john",
  "model_usage": [
    {
      "model_name": "gpt-oss:120b",
      "request_count": 15,
      "total_tokens": 8000
    },
    {
      "model_name": "deepseek-v3.1:671b",
      "request_count": 10,
      "total_tokens": 7000
    }
  ],
  "period": { ... }
}
```

---

### Model Mappings

#### Create Mapping

```bash
POST /admin/model-mappings
```

**Body:**

```json
{
  "display_name": "gpt-oss:120b",
  "real_name": "gpt-oss:120b-cloud",
  "context_length": 128000,
  "capabilities": ["completion", "tools"],
  "node_id": null
}
```

- `node_id` — optional node ID for node-scoped mappings. When set, this mapping only applies when the request is routed to the specified node. Use `null` for global mappings.

#### List Mappings

```bash
GET /admin/model-mappings
```

#### Get Mapping

```bash
GET /admin/model-mappings/{display_name}
```

#### Update Mapping

```bash
PUT /admin/model-mappings/{display_name}
```

#### Delete Mapping

```bash
DELETE /admin/model-mappings/{display_name}
```

---

### Nodes

#### List Nodes

```bash
GET /admin/nodes
```

#### Get Node

```bash
GET /admin/nodes/{id}
```

#### Create Node

```bash
POST /admin/nodes
```

**Body:**

```json
{
  "name": "main-server",
  "base_url": "http://localhost:11434",
  "priority": 100,
  "weight": 10,
  "is_active": true,
  "code": "trmix",
  "node_type": "ollama",
  "warmup_enabled": true
}
```

- `code` — unique short identifier for node prefix routing (`^[a-z0-9_-]{1,30}$`)
- `node_type` — `"ollama"` or `"vllm"`
- `warmup_enabled` — whether to run model warmup on this node

#### Update Node

```bash
PUT /admin/nodes/{id}
```

#### Delete Node

```bash
DELETE /admin/nodes/{id}
```

#### Toggle Activation

```bash
PATCH /admin/nodes/{id}/toggle
```

**Response:**

```json
{
  "id": 1,
  "name": "main-server",
  "is_active": false
}
```

#### Trigger Discovery

```bash
POST /admin/nodes/{id}/discover
```

Manually triggers model discovery on the node.

#### Reorder Priorities (Batch)

```bash
PATCH /admin/nodes/batch/priority
```

**Body:**

```json
{
  "priorities": [
    {"id": 1, "priority": 200},
    {"id": 2, "priority": 100}
  ]
}
```

Updates the priority of multiple nodes in a single request. Used by the drag-and-drop reordering in the admin panel.

---

### Model Groups

#### List Groups

```bash
GET /admin/model-groups
```

#### Get Group

```bash
GET /admin/model-groups/{name}
```

#### Create Group

```bash
POST /admin/model-groups
```

**Body:**

```json
{
  "name": "coding",
  "strategy": "round_robin",
  "description": "Code generation models"
}
```

**Strategies:** `round_robin`, `weighted`, `priority`

#### Update Group

```bash
PUT /admin/model-groups/{name}
```

#### Delete Group

```bash
DELETE /admin/model-groups/{name}
```

#### List Members

```bash
GET /admin/model-groups/{name}/members
```

#### Add Member

```bash
POST /admin/model-groups/{name}/members
```

**Body:**

```json
{
  "model_display_name": "qwen3-coder:480b",
  "priority": 1,
  "weight": 10,
  "capability_tags": ["tools"]
}
```

#### Update Member

```bash
PUT /admin/model-groups/{name}/members/{id}
```

#### Remove Member

```bash
DELETE /admin/model-groups/{name}/members/{id}
```

#### Reorder Members

```bash
POST /admin/model-groups/{name}/members/reorder
```

**Body:** `{"member_ids": [3, 1, 2]}`

---

### System Config

#### List Config

```bash
GET /admin/config
```

#### Get Config Value

```bash
GET /admin/config/{key}
```

#### Set Config

```bash
POST /admin/config
```

**Body:**

```json
{
  "key": "default_timeout",
  "value": "30",
  "description": "Default proxy timeout in seconds"
}
```

#### Delete Config

```bash
DELETE /admin/config/{key}
```

---

### Tool Sets

#### List Tool Sets

```bash
GET /admin/tool-sets
```

#### Get Tool Set

```bash
GET /admin/tool-sets/{id}
```

#### Create Tool Set

```bash
POST /admin/tool-sets
```

**Body:**

```json
{
  "name": "web-search",
  "tools": ["web_search", "browser"],
  "description": "Web search and browser tools"
}
```

#### Update Tool Set

```bash
PUT /admin/tool-sets/{id}
```

#### Delete Tool Set

```bash
DELETE /admin/tool-sets/{id}
```

---

### Audit Logs

#### List Audit Logs

```bash
GET /admin/audit-logs?limit=50&offset=0&action=create&entity_type=user
```

**Query params:**
- `limit` — default 50
- `offset` — default 0
- `action` — filter by action type
- `entity_type` — filter by entity type
- `performed_by` — filter by username

**Response:**

```json
{
  "logs": [
    {
      "id": 1,
      "action": "create",
      "entity_type": "user",
      "entity_id": "john",
      "performed_by": "admin",
      "details": { ... },
      "created_at": "2024-01-20T10:30:00"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

---

## Grafana Assistant

Grafana-native LLM Assistant compatibility endpoints.

### Chats

#### List Chats

```bash
GET /grafana/assistant/chats
```

**Headers:** `Authorization: Bearer <jwt-token>`

**Response:**

```json
[
  {
    "id": "chat-1",
    "title": "Python question",
    "messages": [
      {"role": "user", "content": "How do I use list comprehensions?"},
      {"role": "assistant", "content": "List comprehensions provide a concise way..."}
    ],
    "created_at": "2024-01-20T10:30:00",
    "updated_at": "2024-01-20T10:31:00"
  }
]
```

#### Create Chat

```bash
POST /grafana/assistant/chats
```

**Body:**

```json
{
  "message": "How do I use list comprehensions?"
}
```

**Response:** Same chat object with assistant reply appended.

---

### Chat Stream

```bash
POST /grafana/assistant/chat/stream
```

**Body:**

```json
{
  "message": "Tell me a story"
}
```

**Response:** SSE stream with `data:` lines.

---

### Config

#### Get LLM Config

```bash
GET /grafana/assistant/config
```

**Response:**

```json
{
  "model": "gpt-oss:120b",
  "temperature": 0.7,
  "max_tokens": 2048
}
```

#### Update LLM Config

```bash
POST /grafana/assistant/config
```

**Body:**

```json
{
  "model": "gpt-oss:120b",
  "temperature": 0.7,
  "max_tokens": 2048
}
```

---

### Discovery

#### Get Infrastructure Discovery Status

```bash
GET /grafana/assistant/discovery
```

**Response:**

```json
{
  "status": "completed",
  "nodes_discovered": 3,
  "models_discovered": 12,
  "last_run": "2024-01-20T10:30:00"
}
```

---

For the full architecture overview, see [`ARCHITECTURE.md`](ARCHITECTURE.md).
