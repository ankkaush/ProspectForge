# Deployment (Step 23)

Deployment readiness audit, the artifacts prepared, and the how-to guide for the live deploy -
not yet performed. Default host: **Render**, per the original project plan.

## Readiness audit

**Is a Dockerfile needed?** Yes - Render (and most hosts) build and run the app from a
container. None existed before this step; added.

**Does the app have everything required to run inside a container?** Yes, with one real fix
made this step. Config is already fully env-var-driven (`pydantic-settings`) - confirmed
directly that `Settings` falls back cleanly to real OS environment variables when no `.env`
file is present (which is exactly the container's situation, since `.env` is git/docker-ignored
and never gets copied into the image). Seed CSVs and ICP/persona YAML configs are loaded via
relative paths, so they work identically once baked into the image. The one gap: interactive
API docs (`/docs`, `/redoc`) were unconditionally enabled - not a data leak (every endpoint
still requires the API key except `/health`), but disabled in production anyway on general
principle, via `app/main.py::docs_urls_for_environment()`.

**Is Docker Compose useful for local development?** Yes, already was for Postgres.
`docker-compose.yml` now also has an optional `app` service (`docker compose --profile full
up`) for testing the actual container locally, without changing the default day-to-day
workflow (`uvicorn app.main:app --reload` against the same Postgres service is still faster to
iterate with).

**Are environment variables handled correctly?** Yes, verified directly (see above) - no code
change needed here.

**Is the FastAPI app configured correctly for production?** No CORS middleware (correct - this
is an API called by an operator/CLI/scheduler, not a browser frontend, so none is needed). Docs
exposure fixed (see above). No other production-specific gaps found.

**Does the app need Gunicorn/multiple Uvicorn workers?** No. This is explicitly "not a
public-facing service" (per the roadmap) - a manually- or schedule-triggered internal tool, not
something expected to serve concurrent public traffic. Plain `uvicorn app.main:app` is
sufficient; adding Gunicorn now would be complexity with no present justification. Revisit if
that assumption ever changes.

**Are database migrations handled correctly during deployment?** Not automated before this
step - a real gap. Fixed: `docker-entrypoint.sh` runs `alembic upgrade head` before starting
the server, on every container start. This is a no-op when the schema is already current, so
it's safe on every restart, not just the first deploy - and it's host-agnostic (works
identically on Render, Fly.io, Railway, or a bare `docker run`) rather than relying on a
Render-specific "pre-deploy command" dashboard field.

**What should the health check endpoint be?** `/health` already exists (checks DB connectivity,
not just process liveness) - no work needed, just configured as Render's health check path in
`render.yaml`.

**Background workers or scheduled jobs?** The pipeline is triggered via `POST /runs` or the
CLI - there's no in-app scheduler, matching the roadmap's own framing ("a scheduled or
manually-triggered pipeline run"). For a schedule, the recommended mechanism is a **Render Cron
Job** (a separate Render service type, native scheduling, no in-app code needed) running
`python -m prospectforge.cli start-run --icp-config-id saas-fictional-v1` against the same
database. Not included in `render.yaml` by default - add it once you've decided on a real
cadence; manual triggering via `POST /runs` works today with zero extra setup.

**What needs to be configured as secrets on the host?** See `render.yaml` - every `sync: false`
entry is a value Render will prompt for in its dashboard and never stores in this repo:
`PROSPECTFORGE_API_KEY` (generate a **new** value for production - never reuse the local `.env`
one), `APOLLO_API_KEY`, `ANTHROPIC_API_KEY`, `HUBSPOT_API_KEY`, `SENTRY_DSN` (all optional -
each integration is simply disabled without its key, same as locally). `DATABASE_URL` is
provisioned automatically from Render's managed Postgres add-on, never entered by hand.

## Artifacts prepared this step

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the app image - `python:3.9-slim` (matches the interpreter this project has run on throughout), `psycopg-binary` needs no extra system packages |
| `docker-entrypoint.sh` | Runs `alembic upgrade head`, then starts `uvicorn` bound to `$PORT` |
| `.dockerignore` | Keeps `.env`, `.git`, local DB files, and dev-only files out of the image |
| `docker-compose.yml` | Extended with an optional `app` service (`--profile full`) for local container-parity testing |
| `render.yaml` | Render Blueprint - defines the web service + managed Postgres, every secret marked `sync: false` |
| `app/main.py` | Docs disabled in production (`docs_urls_for_environment()`) |

**Verified locally (not a live deployment):** `docker build` succeeds; the built image, run
against a throwaway Postgres database on the existing dev Docker network, correctly ran all
three pending Alembic migrations in order, started the server, answered `/health`, and
processed a real `POST /runs` through discovery and prefilter before failing cleanly and
clearly on a missing `APOLLO_API_KEY` (expected - none was provided to the test container) -
exactly the fail-fast behavior from Step 5, now confirmed to hold inside the container too.

## How to actually deploy (when you're ready)

1. Push this repository to GitHub (after the audit/first-commit review in
   `docs/pre-public-audit.md`).
2. In the Render dashboard: **New** -> **Blueprint** -> connect the GitHub repo -> Render reads
   `render.yaml` and provisions the web service + Postgres database together.
3. Render will prompt for each `sync: false` value - generate a fresh
   `PROSPECTFORGE_API_KEY` (e.g. `openssl rand -hex 32`); leave the optional provider keys
   blank unless you're ready to use that integration live.
4. First deploy: Render builds the Dockerfile, runs migrations via the entrypoint script, and
   starts the app - watch the deploy log for the same three migrations seen in local testing.
5. Confirm `GET https://<your-app>.onrender.com/health` returns `{"status": "ok"}`.
6. Trigger one real pipeline run against the deployed instance (`POST /runs` with your new API
   key) - this is Step 23's actual exit criteria, not something achievable from a laptop.

## Alternatives considered

Fly.io and Railway remain viable (Fly.io in particular doesn't require a git remote at all, and
was proposed earlier in this project as a lower-friction option) - kept as documented
alternatives per the original project plan's Render default, not adopted, since there's no
concrete technical reason to switch. `render.yaml` is Render-specific; a Fly.io or Railway
deploy would use the same `Dockerfile` directly (both build from a Dockerfile natively) with a
platform-specific config file in its place - the application artifacts themselves
(`Dockerfile`, `docker-entrypoint.sh`) aren't Render-specific and don't need to change if the
hosting decision changes later.
