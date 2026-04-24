# Quick Start Guide

Get the Model Maestro gateway running in under 5 minutes.

---

## Prerequisites

- Docker
- Docker Compose

---

## 1. Launch the Full Stack

```bash
# Clone the repository
git clone <repository-url> && cd model-maestro

# Configure environment
cp .env.example .env

# Start all services (PostgreSQL + Redis + FastAPI + Next.js)
docker compose -f docker-compose.dev.yml up --build -d
```

---

## 2. Seed the Database

```bash
docker exec maestro python -m app.seeder
```

---

## 3. Verify Everything is Running

| Service | URL | What to check |
|---|---|---|
| API | `http://localhost:8000` | Should return JSON with project info |
| Admin Panel | `http://localhost:3000` | Login with `admin` / `ADMIN_PASSWORD` from `.env` |
| API Docs | `http://localhost:8000/api/docs` | Basic-auth protected Swagger UI |

**Health check:**
```bash
curl http://localhost:8000/health
```

---

## 4. Create Your First User

```bash
# Create a user
curl -X POST http://localhost:8000/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "developer"}'

# The response contains the user's JWT token. Save it.
```

---

## 5. Test the LLM API

```bash
# Set the token from the previous step
export TOKEN="user-jwt-token-here"

# List available models
curl http://localhost:8000/api/tags \
  -H "Authorization: Bearer $TOKEN"

# Chat completion
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-oss:120b",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## 6. Add Model Mappings

Model mappings translate display names to real model names on Ollama.

```bash
curl -X POST http://localhost:8000/admin/model-mappings \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "gpt-oss:120b",
    "real_name": "gpt-oss:120b-cloud",
    "context_length": 128000
  }'
```

Mappings take effect immediately — no restart required.

---

## 7. Add Ollama Nodes

```bash
curl -X POST http://localhost:8000/admin/nodes \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "main-server",
    "base_url": "http://host.docker.internal:11434",
    "priority": 100
  }'
```

The gateway will automatically discover models on the node and run health checks.

---

## 8. Assign Models to Users

```bash
# Assign specific models
curl -X POST http://localhost:8000/admin/users/developer/models \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"models": ["gpt-oss:120b", "deepseek-v3.1:671b"]}'

# Or grant access to all models
curl -X POST http://localhost:8000/admin/users/developer/models/all \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

---

## 9. Set Usage Limits (Optional)

```bash
curl -X POST http://localhost:8000/admin/users/developer/limits \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"request_limit": 1000, "token_limit": 1000000}'
```

Use `null` for unlimited.

---

## 10. OpenAI Compatible Usage

Point your IDE (Cursor, Antigravity, Claude Code) to:

```
Base URL: http://localhost:8000/v1
API Key: Bearer <user-jwt-token>
Model: gpt-oss:120b
```

Or test via curl:

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

## Common Commands

```bash
# View logs
docker compose -f docker-compose.dev.yml logs -f maestro

# Restart the API
docker compose -f docker-compose.dev.yml restart maestro

# Run migrations manually
docker exec maestro alembic upgrade head

# Re-run seeds
docker exec maestro python -m app.seeder --reset
docker exec maestro python -m app.seeder

# Clear Redis cache
docker exec maestro python scripts/clear_cache.py

# Stop everything
docker compose -f docker-compose.dev.yml down
```

---

## Troubleshooting

**PostgreSQL not connecting:**
- Check `DATABASE_URL` in `.env` matches the Docker Compose credentials.
- Verify the container is healthy: `docker exec maestro-postgres pg_isready -U maestro_user -d maestro`

**Redis not connecting:**
- Check `REDIS_URL` points to `redis://redis:6379/0` inside Docker.
- Verify: `docker exec maestro-redis redis-cli ping` → should return `PONG`

**Migration errors on startup:**
- The `docker-entrypoint.sh` runs `alembic upgrade head` automatically.
- If it fails, check the Postgres container is fully started before the API.
- In `docker-compose.dev.yml`, `depends_on` with `condition: service_healthy` handles this.

---

## Security Reminders

- Change `JWT_SECRET_KEY` before deploying to production.
- Change `ADMIN_TOKEN` to a strong random string.
- Use HTTPS in production.
- Restrict `allow_origins` in CORS middleware to your frontend URL.

---

For the full setup guide, see [`docs/SETUP.md`](docs/SETUP.md).
