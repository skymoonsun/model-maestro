#!/bin/bash
# Run Alembic migrations inside Docker container

set -e

echo "Running database migrations..."
alembic upgrade head

echo "Migrations completed successfully!"

