#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head
echo "Migrations completed."

# Execute the command as-is (for uvicorn server)
exec "$@"

