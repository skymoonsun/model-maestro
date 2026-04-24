# Setup Guide

Complete setup instructions for Model Maestro.

---

## Table of Contents

- [Docker Compose (Recommended)](#docker-compose-recommended)
- [Local Development](#local-development)
- [Production Deployment](#production-deployment)
- [Environment Variables](#environment-variables)
- [Database Migrations](#database-migrations)
- [Database Seeding](#database-seeding)

---

## Docker Compose (Recommended)

### Development Stack

The development stack includes PostgreSQL, Redis, FastAPI and the Next.js frontend.

```bash
git clone <repository-url> && cd model-maestro
cp .env.example .env
# Edit .env with your settings
docker compose -f docker-compose.dev.yml up --build -d
```

Services:
- `maestro-postgres` — PostgreSQL 15 on port 5432
- `maestro-redis` — Redis 7 on port 6379
- `maestro` — FastAPI on port 8000
- `maestro-frontend` — Next.js on port 3000

### Production Stack

The production stack only includes the FastAPI and frontend containers. You must provide your own PostgreSQL and Redis instances.

```bash
docker compose up --build -d
```

---

## Local Development

### Prerequisites

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Node.js 20+ (for frontend)

### 1. Backend

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
cp .env.example .env
# Edit .env

# Run migrations
alembic upgrade head

# Run seeds
python -m app.seeder

# Start server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend will be available at `http://localhost:3000`.

---

## Production Deployment

### Security Checklist

- [ ] Change `JWT_SECRET_KEY` to a cryptographically secure random string.
- [ ] Change `ADMIN_TOKEN` to a strong random string.
- [ ] Change `ADMIN_PASSWORD` from the default.
- [ ] Change `DOCS_PASSWORD` from the default.
- [ ] Use HTTPS (reverse proxy with TLS termination).
- [ ] Restrict `allow_origins` in CORS middleware to your frontend URL.
- [ ] Use a dedicated PostgreSQL instance with strong credentials.
- [ ] Use a dedicated Redis instance with AUTH enabled.
- [ ] Enable firewall rules to expose only necessary ports.

### Reverse Proxy (Nginx Example)

```nginx
server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OLLAMA_BASE_URL` | Yes | `http://localhost:11434` | Base URL for Ollama |
| `JWT_SECRET_KEY` | Yes | — | Secret key for JWT signing |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `DATABASE_URL` | Yes | — | PostgreSQL connection string |
| `REDIS_URL` | Yes | — | Redis connection string |
| `ADMIN_TOKEN` | Yes | — | Token for admin endpoints |
| `ADMIN_USERNAME` | No | `admin` | Admin panel username |
| `ADMIN_PASSWORD` | No | `admin` | Admin panel password |
| `DOCS_USERNAME` | No | `admin` | Swagger/ReDoc username |
| `DOCS_PASSWORD` | No | `changeme` | Swagger/ReDoc password |

Example `.env`:

```env
OLLAMA_BASE_URL=http://host.docker.internal:11434
JWT_SECRET_KEY=change-this-to-a-strong-secret
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://maestro_user:maestro_password@postgres:5432/maestro
REDIS_URL=redis://redis:6379/0
ADMIN_TOKEN=change-this-for-production
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin
DOCS_USERNAME=admin
DOCS_PASSWORD=admin
```

---

## Database Migrations

Model Maestro uses Alembic for database migrations. Migrations run automatically when the Docker container starts via `docker-entrypoint.sh`.

### Manual Migration

```bash
# Inside the container
docker exec maestro alembic upgrade head

# Or locally (with alembic installed)
alembic upgrade head
```

### Create a New Migration

```bash
alembic revision -m "add new feature table"
# Edit the generated file in alembic/versions/
alembic upgrade head
```

---

## Database Seeding

Seed files are in `app/seeds/` and run via `app/seeder.py`.

### Run Seeds

```bash
# Inside the container
docker exec maestro python -m app.seeder

# Or locally
python -m app.seeder
```

### Seed Status

```bash
docker exec maestro python -m app.seeder --status
```

### Reset and Re-run Seeds

```bash
docker exec maestro python -m app.seeder --reset
docker exec maestro python -m app.seeder
```

### Reset All (including data)

```bash
docker exec maestro python -m app.seeder --reset-all
```

---

## Troubleshooting

### PostgreSQL Connection Refused

- Verify the container is running: `docker ps`
- Check health: `docker exec maestro-postgres pg_isready -U maestro_user -d maestro`
- Verify `DATABASE_URL` matches Docker Compose credentials.

### Redis Connection Refused

- Verify the container is running: `docker ps`
- Test: `docker exec maestro-redis redis-cli ping`
- Verify `REDIS_URL` points to the correct host.

### Migration Fails on Startup

- The API container depends on Postgres being healthy. If Postgres is slow to start, the API may fail its first migration attempt.
- Restart the API container: `docker compose -f docker-compose.dev.yml restart maestro`
- Or run manually: `docker exec maestro alembic upgrade head`

### Seed History Table Missing

If you see `relation "seed_history" does not exist`, the `b5af99d808ad_model_group.py` migration was run on a fresh database where the table had not been created yet. This has been fixed in the migration file with `DROP TABLE IF EXISTS`. Restart the stack.
