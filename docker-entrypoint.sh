#!/bin/sh

set -e

echo "Waiting for database..."

until alembic current > /dev/null 2>&1
do
    echo "Database is not ready yet..."
    sleep 2
done

echo "Database is ready."

# echo "Running database migrations..."
# alembic upgrade head

# echo "Seeding sample users..."
# python -m src.insert_user

echo "Starting FastAPI..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8000