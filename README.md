# ProspectForge

A production-grade B2B outbound prospecting pipeline — ICP definition, account discovery,
enrichment, AI-assisted company research, decision-maker discovery, deterministic
qualification and prioritization, human review, and CRM sync.

Built as a learning project following an explicit 26-step roadmap (currently through Step 24
of 26 — documentation). Every architectural decision is written up in
[`docs/adr/`](docs/adr/README.md), with alternatives considered and consequences, at the time
it was made.

**Live demo:** https://prospectforge.onrender.com/health (deployed via the artifacts in this
repo — see [`docs/deployment.md`](docs/deployment.md); this is an internal ops tool behind an
API key, not a public-facing app, so only `/health` is meaningful to visit directly).

## What it does

```
ICP config → discovery → cheap pre-filter → enrichment → full fit evaluation →
AI-assisted research → decision-maker discovery → contact enrichment → dedup →
deterministic qualification → prioritization → human review → CRM sync
```

Every external integration (Apollo, Anthropic, HubSpot) sits behind a port/adapter interface,
and every stage except one has a CSV-backed (or, for AI rationale, fully deterministic)
fallback as the active default — the full test suite (442 tests) runs with **zero API keys**.
The one exception: account **enrichment** always calls the real Apollo API, since that specific
endpoint is accessible on Apollo's free tier and a stand-in was never needed (see ADR-003's
addendum) — so a full ten-stage `POST /runs` needs at minimum a free `APOLLO_API_KEY` to get
past that one stage. Discovery and pre-filtering alone run against the bundled seed data with
zero keys at all. Every other provider is genuinely opt-in, switched on by adding one
environment variable, no code change.

See [`docs/architecture.md`](docs/architecture.md) for the full module map and design
reasoning, and [`docs/data-model.md`](docs/data-model.md) for every record's fields and status
state machines.

## Quickstart

```bash
git clone https://github.com/ankkaush/ProspectForge.git
cd ProspectForge

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Open .env and set PROSPECTFORGE_API_KEY to any long random string
# (e.g. `openssl rand -hex 32`). Every other setting already has a working
# default — no other API key is required to run the full pipeline against
# the bundled fictional seed data.

docker compose up -d db     # starts a local Postgres in Docker
alembic upgrade head        # applies the schema

uvicorn app.main:app --reload
```

Then, in another terminal:

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl -X POST http://localhost:8000/runs \
  -H "Authorization: Bearer <the value you put in .env>" \
  -H "Content-Type: application/json" \
  -d '{"icp_config_id": "saas-fictional-v1"}'
```

Without `APOLLO_API_KEY` set, this runs discovery and pre-filtering against the bundled
fictional seed data, then stops cleanly at the enrichment stage with a clear
`"APOLLO_API_KEY is not set"` error in the response — that's the app's fail-fast design
working correctly, not a bug (see `docs/runbook.md`). Add a free Apollo API key
(`app.apollo.io` → Settings → API) to `.env` to see all ten stages complete.

### Or via the CLI

```bash
python -m prospectforge.cli start-run --icp-config-id saas-fictional-v1
python -m prospectforge.cli review-queue                       # see what's awaiting human review
python -m prospectforge.cli approve --prospect-id <uuid>
python -m prospectforge.cli sync-to-crm                         # requires HUBSPOT_API_KEY
python -m prospectforge.cli run-summary --run-id <uuid>
```

Every CLI command is listed at the top of [`prospectforge/cli.py`](prospectforge/cli.py).

## Running the tests

```bash
python -m pytest                                          # SQLite, zero setup
DATABASE_URL=postgresql+psycopg://prospectforge:prospectforge@localhost:5432/prospectforge \
  python -m pytest                                         # against real Postgres (needs `docker compose up -d db` first)
```

(`python -m pytest`, not bare `pytest` — this project has no `pyproject.toml`/`pytest.ini`
setting `rootdir`, and `python -m` is what puts the project root on `sys.path` so `tests/
conftest.py`'s `from app import db` resolves.)

442 tests: unit tests per module, integration tests running the whole pipeline end to end
(`tests/integration/test_full_pipeline.py`), and deliberate chaos/fault-injection tests
(`tests/integration/test_chaos.py`) — see
[`docs/failure-scenario-coverage.md`](docs/failure-scenario-coverage.md) for what's covered.

## Enabling a real integration

Every provider is optional and independently switchable — set the corresponding key in `.env`
and (where applicable) flip its `*_PROVIDER` setting from `csv` to `apollo`:

| Integration | Env var | Notes |
|---|---|---|
| Apollo (discovery/people-search/contact-match) | `APOLLO_API_KEY` | Free-tier search endpoints are gated — see ADR-003's addendum. Enrichment (not search) works on the free tier. |
| Anthropic (research, optional rationale phrasing) | `ANTHROPIC_API_KEY` | Qualification's verdict is always deterministic regardless — see Step 15's correction in `qualification/service.py`. |
| HubSpot (CRM sync) | `HUBSPOT_API_KEY` | Private app token from a developer/sandbox account. |
| Sentry (error tracking) | `SENTRY_DSN` | Fully optional — disabled cleanly without it. |

Full list and reasoning in [`.env.example`](.env.example).

## Deployment

See [`docs/deployment.md`](docs/deployment.md) — `Dockerfile`, `docker-entrypoint.sh` (runs
Alembic migrations, then starts the server), and a `render.yaml` blueprint for Render (the
default host; every secret marked so Render prompts for it rather than storing anything in
this repo). Deployed and live-verified as of Step 23.

## When something's wrong

See [`docs/runbook.md`](docs/runbook.md) — what to check for a failed run, a stuck account,
a degraded provider, a review-queue backlog, or a GDPR erasure request.

## Documentation map

| Doc | What's in it |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Module boundaries, data flow, the ports/adapters pattern |
| [`docs/data-model.md`](docs/data-model.md) | Every record's fields, and the account/contact status state machines |
| [`docs/adr/`](docs/adr/README.md) | Every architectural decision, with alternatives considered |
| [`docs/runbook.md`](docs/runbook.md) | Operational guide — what to check when something fails |
| [`docs/security-privacy-review.md`](docs/security-privacy-review.md) | GDPR posture, PII-in-logs audit, dependency check (Step 20) |
| [`docs/failure-scenario-coverage.md`](docs/failure-scenario-coverage.md) | Every named failure scenario mapped to the test that proves it (Step 21) |
| [`docs/pre-public-audit.md`](docs/pre-public-audit.md) | The audit performed before this repo went public (Step 23) |
| [`docs/deployment.md`](docs/deployment.md) | Deployment readiness audit and the live Render deployment |
| [`docs/reusability.md`](docs/reusability.md) | Honest audit of what a second client engagement would actually reuse (Step 25) |
| [`docs/demo.md`](docs/demo.md) | One real prospect's actual journey, ICP to CRM record, with real data at every stage (Step 26) — [published version](https://claude.ai/code/artifact/0cbd034e-ebfd-47db-94a0-e26d2ee22656) |

## Project scope

This project deliberately stops at CRM sync — it does not attempt outreach, sequencing, or
reply handling. See [`docs/adr/README.md`](docs/adr/README.md) for why each major dependency
(Apollo, Anthropic, HubSpot, Render) was chosen, and what would change to swap any of them.
