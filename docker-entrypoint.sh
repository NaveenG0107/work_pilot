#!/bin/sh
set -e

# Preserve one-off container commands such as:
# docker compose run --rm fastapi alembic check
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "[DB] Preparing database..."
python -m src.utils.database_migration

echo "[APP] Starting FastAPI..."
exec uvicorn src.main:app --host 0.0.0.0 --port "${PORT:-8000}"
