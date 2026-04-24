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
  - [Models](#models)
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
      "created_at": "2024-01-20T10:30:00"
    }
  ],
  "total_returned": 1,
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
  "capabilities": ["completion", "tools"]
}
```

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
  "is_active": true
}
```

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

For the full architecture overview, see [`ARCHITECTURE.md`](ARCHITECTURE.md).
