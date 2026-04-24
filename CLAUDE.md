# CLAUDE.md — Model Maestro

## Proje Hakkında
Unified LLM Gateway. Farklı provider'lardan (Ollama, OpenAI, vs.) gelen modelleri tek API altında birleştirir.
IDE'ler (Cursor, Antigravity, Claude Code) ve diğer araçların standart API formatları üzerinden çoklu LLM'lere erişmesini sağlar.

## Tech Stack
- **Python 3.10+**, FastAPI, Uvicorn
- **Database**: PostgreSQL (asyncpg + SQLAlchemy async)
- **Cache**: Redis
- **Auth**: JWT (PyJWT)
- **HTTP Client**: httpx (HTTP/2)
- **Migration**: Alembic

## Proje Yapısı
```
app/
  main.py           → FastAPI app, router'lar
  proxy.py          → Proxy logic, model routing
  config.py         → Pydantic settings
  auth.py           → JWT authentication
  models.py         → Pydantic models
  models_db.py      → SQLAlchemy models
  database.py       → DB connection
  redis.py          → Redis client
  load_balancer.py  → Node load balancing
  node_manager.py   → Node health/management
  user_manager.py   → User CRUD
  openclaw.py       → OpenClaw integration
  repositories/     → Data access layer
  services/         → Business logic
  seeds/            → DB seed data
frontend/           → Admin dashboard
```

## Conventions
- **Async everywhere** — tüm endpoint'ler async def
- **Repository pattern** — data access `repositories/` altında
- **Service layer** — business logic `services/` altında
- **Conventional commits**: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`
- **Type hints** zorunlu
- **Docstring'ler** public fonksiyonlarda

## Test
```bash
python -m pytest tests/ -v
```
Test yoksa `tests/` dizini oluştur, `conftest.py` ile fixtures yaz.
pytest + httpx AsyncClient kullan.

## Dikkat
- `.env` dosyasını commit'leme
- Database migration'ları Alembic ile
- Redis bağlantısı gerektiren test'lerde mock kullan
- Proxy endpoint'lerinde timeout handling kritik

---

## Living Knowledge
> Bu bölüm agent'lar tarafından güncellenir. Proje üzerinde çalışıldıkça öğrenilen bilgiler buraya eklenir.

### Mimari Kararlar
<!-- Neden X yerine Y seçildi? Pattern tercihler, design decision'lar -->

### Gotcha'lar & Tuzaklar
<!-- Bilmeden düşülen hatalar, dikkat edilmesi gerekenler -->

### Çözülen Problemler
<!-- Bug fix'ler ve çözüm yaklaşımları — gelecekte referans için -->

### Performans Notları
<!-- Bottleneck'ler, optimizasyon fırsatları, benchmark'lar -->
