.PHONY: help dev-up dev-down dev-restart dev-logs dev-build dev-clean prod-up prod-down prod-restart prod-logs

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

help: ## Show this help message
	@echo "$(BLUE)Ollama Proxy API - Docker Management$(NC)"
	@echo ""
	@echo "$(GREEN)Development Commands:$(NC)"
	@grep -E '^dev-[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Production Commands:$(NC)"
	@grep -E '^prod-[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Database Commands:$(NC)"
	@grep -E '^db-[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(GREEN)Monitoring Commands:$(NC)"
	@grep -E '^logs-[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-20s$(NC) %s\n", $$1, $$2}'

# ============================================================================
# Development Environment
# ============================================================================

dev-up: ## Start development environment (PostgreSQL, Redis, API, Celery)
	@echo "$(GREEN)Starting development environment...$(NC)"
	docker-compose -f docker-compose.dev.yml up -d
	@echo "$(GREEN)✓ Development environment started$(NC)"
	@echo ""
	@echo "$(YELLOW)Services:$(NC)"
	@echo "  API:        http://localhost:8000"
	@echo "  Docs:       http://localhost:8000/api/docs"
	@echo "  PostgreSQL: localhost:5432"
	@echo "  Redis:      localhost:6379"
	@echo ""
	@echo "$(YELLOW)Next steps:$(NC)"
	@echo "  1. Initialize database: make db-init"
	@echo "  2. View logs: make dev-logs"
	@echo "  3. Check status: make status"

dev-down: ## Stop development environment
	@echo "$(YELLOW)Stopping development environment...$(NC)"
	docker-compose -f docker-compose.dev.yml down
	@echo "$(GREEN)✓ Development environment stopped$(NC)"

dev-restart: ## Restart development environment
	@echo "$(YELLOW)Restarting development environment...$(NC)"
	docker-compose -f docker-compose.dev.yml restart
	@echo "$(GREEN)✓ Development environment restarted$(NC)"

dev-logs: ## Show all development logs (follow)
	docker-compose -f docker-compose.dev.yml logs -f

dev-build: ## Rebuild development containers
	@echo "$(YELLOW)Rebuilding development containers...$(NC)"
	docker-compose -f docker-compose.dev.yml build --no-cache
	@echo "$(GREEN)✓ Containers rebuilt$(NC)"

dev-clean: ## Stop and remove all development containers and volumes
	@echo "$(RED)Cleaning development environment (removing volumes)...$(NC)"
	@echo "$(RED)WARNING: This will delete all data including PostgreSQL and Redis!$(NC)"
	@read -p "Are you sure? (yes/no): " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		docker-compose -f docker-compose.dev.yml down -v; \
		echo "$(GREEN)✓ Development environment cleaned$(NC)"; \
	else \
		echo "$(YELLOW)Cancelled$(NC)"; \
	fi

dev-shell: ## Open shell in API container
	docker exec -it ollama-proxy /bin/bash

# ============================================================================
# Production Environment
# ============================================================================

prod-up: ## Start production environment
	@echo "$(GREEN)Starting production environment...$(NC)"
	docker-compose -f docker-compose.yml up -d
	@echo "$(GREEN)✓ Production environment started$(NC)"

prod-down: ## Stop production environment
	@echo "$(YELLOW)Stopping production environment...$(NC)"
	docker-compose -f docker-compose.yml down
	@echo "$(GREEN)✓ Production environment stopped$(NC)"

prod-restart: ## Restart production environment
	@echo "$(YELLOW)Restarting production environment...$(NC)"
	docker-compose -f docker-compose.yml restart
	@echo "$(GREEN)✓ Production environment restarted$(NC)"

prod-logs: ## Show production logs (follow)
	docker-compose -f docker-compose.yml logs -f

prod-build: ## Rebuild production containers
	@echo "$(YELLOW)Rebuilding production containers...$(NC)"
	docker-compose -f docker-compose.yml build --no-cache
	@echo "$(GREEN)✓ Containers rebuilt$(NC)"

# ============================================================================
# Database Management
# ============================================================================

db-init: ## Initialize database (run migrations)
	@echo "$(YELLOW)Waiting for PostgreSQL to be ready...$(NC)"
	@sleep 5
	@echo "$(GREEN)Running database migrations...$(NC)"
	docker exec ollama-proxy alembic upgrade head
	@echo "$(GREEN)✓ Database initialized$(NC)"

db-migrate: ## Create new migration (provide MESSAGE="migration message")
	@if [ -z "$(MESSAGE)" ]; then \
		echo "$(RED)Error: Please provide MESSAGE=\"your migration message\"$(NC)"; \
		exit 1; \
	fi
	@echo "$(YELLOW)Creating new migration: $(MESSAGE)$(NC)"
	docker exec ollama-proxy alembic revision --autogenerate -m "$(MESSAGE)"
	@echo "$(GREEN)✓ Migration created$(NC)"

db-upgrade: ## Upgrade database to latest migration
	@echo "$(GREEN)Upgrading database...$(NC)"
	docker exec ollama-proxy alembic upgrade head
	@echo "$(GREEN)✓ Database upgraded$(NC)"

db-downgrade: ## Downgrade database by one migration
	@echo "$(YELLOW)Downgrading database...$(NC)"
	docker exec ollama-proxy alembic downgrade -1
	@echo "$(GREEN)✓ Database downgraded$(NC)"

db-reset: ## Reset database (drops and recreates)
	@echo "$(RED)WARNING: This will delete ALL data in the database!$(NC)"
	@read -p "Are you sure? (yes/no): " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		docker exec ollama-proxy-postgres psql -U ollama_user -d postgres -c "DROP DATABASE IF EXISTS ollama_proxy;"; \
		docker exec ollama-proxy-postgres psql -U ollama_user -d postgres -c "CREATE DATABASE ollama_proxy;"; \
		docker exec ollama-proxy alembic upgrade head; \
		echo "$(GREEN)✓ Database reset complete$(NC)"; \
	else \
		echo "$(YELLOW)Cancelled$(NC)"; \
	fi

db-shell: ## Open PostgreSQL shell
	@echo "$(YELLOW)Opening PostgreSQL shell...$(NC)"
	@echo "Database: ollama_proxy"
	@echo "User: ollama_user"
	@echo ""
	docker exec -it ollama-proxy-postgres psql -U ollama_user -d ollama_proxy

db-backup: ## Backup database to backups/ folder
	@echo "$(GREEN)Creating database backup...$(NC)"
	@mkdir -p backups
	docker exec ollama-proxy-postgres pg_dump -U ollama_user ollama_proxy > backups/backup_$$(date +%Y%m%d_%H%M%S).sql
	@echo "$(GREEN)✓ Backup created in backups/$(NC)"

db-restore: ## Restore database from backup (provide BACKUP_FILE=path/to/backup.sql)
	@if [ -z "$(BACKUP_FILE)" ]; then \
		echo "$(RED)Error: Please provide BACKUP_FILE=path/to/backup.sql$(NC)"; \
		exit 1; \
	fi
	@echo "$(YELLOW)Restoring database from $(BACKUP_FILE)...$(NC)"
	docker exec -i ollama-proxy-postgres psql -U ollama_user ollama_proxy < $(BACKUP_FILE)
	@echo "$(GREEN)✓ Database restored$(NC)"

# ============================================================================
# Redis Management
# ============================================================================

redis-cli: ## Open Redis CLI
	docker exec -it ollama-proxy-redis redis-cli

redis-flush: ## Flush all Redis cache
	@echo "$(RED)WARNING: This will clear ALL Redis cache!$(NC)"
	@read -p "Are you sure? (yes/no): " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		docker exec ollama-proxy-redis redis-cli FLUSHALL; \
		echo "$(GREEN)✓ Redis cache cleared$(NC)"; \
	else \
		echo "$(YELLOW)Cancelled$(NC)"; \
	fi

redis-info: ## Show Redis info
	docker exec ollama-proxy-redis redis-cli INFO

redis-keys: ## List all Redis keys
	docker exec ollama-proxy-redis redis-cli KEYS "*"

# ============================================================================
# Monitoring & Logs
# ============================================================================

status: ## Show status of all services
	@echo "$(BLUE)Service Status:$(NC)"
	docker-compose -f docker-compose.dev.yml ps

logs-api: ## Show API container logs
	docker-compose -f docker-compose.dev.yml logs -f ollama-proxy

logs-celery: ## Show Celery worker logs
	docker-compose -f docker-compose.dev.yml logs -f celery-worker

logs-beat: ## Show Celery beat logs
	docker-compose -f docker-compose.dev.yml logs -f celery-beat

logs-db: ## Show PostgreSQL logs
	docker-compose -f docker-compose.dev.yml logs -f postgres

logs-redis: ## Show Redis logs
	docker-compose -f docker-compose.dev.yml logs -f redis

stats: ## Show container resource usage
	docker stats ollama-proxy ollama-proxy-postgres ollama-proxy-redis celery-worker celery-beat

health: ## Check health of all services
	@echo "$(BLUE)Health Check:$(NC)"
	@echo ""
	@echo "$(YELLOW)API:$(NC)"
	@curl -s http://localhost:8000/health | jq . || echo "$(RED)✗ API not responding$(NC)"
	@echo ""
	@echo "$(YELLOW)PostgreSQL:$(NC)"
	@docker exec ollama-proxy-postgres pg_isready -U ollama_user -d ollama_proxy || echo "$(RED)✗ PostgreSQL not ready$(NC)"
	@echo ""
	@echo "$(YELLOW)Redis:$(NC)"
	@docker exec ollama-proxy-redis redis-cli ping || echo "$(RED)✗ Redis not responding$(NC)"

# ============================================================================
# Quick Setup
# ============================================================================

setup: dev-up db-init ## Complete setup (start containers + initialize database)
	@echo ""
	@echo "$(GREEN)✓ Setup complete!$(NC)"
	@echo ""
	@echo "$(YELLOW)Environment is ready:$(NC)"
	@echo "  API:  http://localhost:8000"
	@echo "  Docs: http://localhost:8000/api/docs (user: admin, pass: dev-docs-password)"
	@echo ""
	@echo "$(YELLOW)Create users via API:$(NC)"
	@echo "  POST /admin/users"
	@echo "  Authorization: Bearer <ADMIN_TOKEN from .env.dev>"
	@echo ""
	@echo "$(YELLOW)Useful commands:$(NC)"
	@echo "  View logs:      make dev-logs"
	@echo "  Check status:   make status"
	@echo "  Health check:   make health"

# ============================================================================
# Maintenance
# ============================================================================

restart-api: ## Restart only API container
	docker-compose -f docker-compose.dev.yml restart ollama-proxy

restart-celery: ## Restart Celery worker and beat
	docker-compose -f docker-compose.dev.yml restart celery-worker celery-beat

restart-db: ## Restart PostgreSQL
	docker-compose -f docker-compose.dev.yml restart postgres

restart-redis: ## Restart Redis
	docker-compose -f docker-compose.dev.yml restart redis

rebuild: dev-build dev-up ## Rebuild and restart all containers
	@echo "$(GREEN)✓ Rebuild complete$(NC)"
