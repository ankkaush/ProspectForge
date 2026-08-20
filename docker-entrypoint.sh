#!/bin/sh
# Runs pending Alembic migrations before starting the app, every time the
# container starts - deliberately host-agnostic (works identically on
# Render, Fly.io, Railway, or a bare `docker run`) rather than relying on
# a platform-specific "pre-deploy command" field. alembic upgrade head is
# a no-op if the schema is already current, so this is safe to run on
# every restart, not just the first deploy.
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting server..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
