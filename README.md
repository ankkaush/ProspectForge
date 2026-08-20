# ProspectForge

A production-grade B2B outbound prospecting pipeline - ICP definition, account discovery,
enrichment, AI-assisted company research, decision-maker discovery, deterministic
qualification and prioritization, human review, and CRM sync - built as a learning project
following an explicit 26-step roadmap (currently through Step 23 of 26). Every architectural
decision is written up in `docs/adr/`.

> **Note:** this README is intentionally minimal for now. A full README (setup walkthrough,
> module-by-module description, runbook) is Step 24 on the project's own roadmap, written
> against the finished system rather than piecemeal mid-build - see `docs/architecture.md` and
> `docs/adr/` in the meantime for the complete design reasoning.

## What it does

ICP config in → discovered accounts → cheap pre-filter → enrichment → full fit evaluation →
AI-assisted research → decision-maker discovery → contact enrichment → dedup → deterministic
qualification → prioritization → human review → CRM sync. Every external integration
(Apollo, Anthropic, HubSpot) sits behind a port/adapter interface with a CSV-backed fallback,
so the pipeline runs and is fully testable with zero API keys.

## Running it locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set PROSPECTFORGE_API_KEY to any long random string.
# Everything else has a working default - no API keys required to run the pipeline
# end-to-end against the bundled fictional seed data.

docker compose up -d db          # local Postgres
alembic upgrade head             # apply the schema

uvicorn app.main:app --reload    # http://localhost:8000/health
```

Or via the CLI:

```bash
python -m prospectforge.cli start-run --icp-config-id saas-fictional-v1
python -m prospectforge.cli review-queue
python -m prospectforge.cli run-summary --run-id <uuid>
```

## Running the tests

```bash
pytest                                                    # SQLite, no setup needed
DATABASE_URL=postgresql+psycopg://prospectforge:prospectforge@localhost:5432/prospectforge \
  pytest                                                  # against real Postgres
```

## Deployment

See `docs/deployment.md` - Dockerfile, `docker-entrypoint.sh` (runs migrations, then starts
the server), and a `render.yaml` blueprint for Render (the default target; not yet deployed).

## Documentation map

- `docs/architecture.md` - module boundaries, data flow
- `docs/adr/` - every architectural decision, with alternatives considered
- `docs/security-privacy-review.md`, `docs/failure-scenario-coverage.md`,
  `docs/pre-public-audit.md`, `docs/deployment.md` - the reliability/security/deployment
  passes (Steps 19-23)
