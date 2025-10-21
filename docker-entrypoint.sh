#!/bin/bash
set -e

# If first arg is a recognized CLI command, run CLI
if [ "$1" = "create-user" ] || [ "$1" = "delete-user" ] || [ "$1" = "refresh-token" ] || [ "$1" = "list-users" ] || [ "$1" = "show-user" ]; then
    exec python cli.py "$@"
fi

# Otherwise, execute the command as-is (for uvicorn server)
exec "$@"

