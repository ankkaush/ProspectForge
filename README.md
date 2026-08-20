# ProspectForge — B2B Outbound Prospecting Pipeline

A backend automation that takes an ideal-customer-profile definition and turns it into a
ranked, evidence-backed list of real companies and contacts ready for a sales rep — discovery,
enrichment, AI-assisted research, decision-maker discovery, deterministic qualification and
prioritization, human review, and CRM sync, with retries, circuit-breaking, and a full audit
trail so a failure is always visible, never silent.

Built as a learning project following an explicit 26-step roadmap, treated with the same rigor
as a real production build: architecture decisions written down as they were made
([`docs/adr/`](docs/adr/README.md)), a completed security/GDPR review, deliberate chaos and
failure-injection testing, and a live deployment — not just a working demo.

**This is a public repository, released under the [MIT License](LICENSE)** — free to use,
modify, and adapt to your own business. See [Configuration](#configuration--credentials) for
setting it up with your own credentials, and [Security considerations](#security--privacy)
for what that means in practice.

**Live deployment**: [prospectforge.onrender.com](https://prospectforge.onrender.com/health)
(`/health` is public; every other endpoint requires an API key — this is an internal ops tool,
not a public-facing app). Deployed on Render per [`docs/deployment.md`](docs/deployment.md).

**Status**: all 26 roadmap steps complete, through Step 26 (end-to-end demonstration) — see
[`docs/demo.md`](docs/demo.md) for a real prospect's full journey traced through the live
database, and [§ Project status](#project-status--roadmap) below for what "complete" does and
doesn't claim.

## 1. What this automation does

```
ICP config (who counts as a good-fit company)
   → discovery: finds real companies matching the ICP
   → cheap pre-filter: rejects obvious non-fits before spending on enrichment
   → enrichment: fills in firmographic detail (tech stack, funding stage, growth signal)
   → full fit evaluation: tiers the account with enrichment data factored in
   → company research: AI-assisted web search for evidence supporting outreach
   → decision-maker discovery: finds the right person to contact at that company
   → contact enrichment: resolves a verified email for that person
   → validation & dedup: merges near-duplicate accounts conservatively
   → qualification: a deterministic verdict - qualified or not, and why
   → prioritization: ranks every qualified prospect by fit, evidence, and seniority
   → human review: a person approves or rejects each ranked prospect
   → CRM sync: only approved prospects are pushed to the CRM, idempotently
```

Every stage after discovery isolates its own failures — one account's enrichment call failing
never blocks the rest of the batch, and nothing is silently dropped: every account's status is
stored, retried automatically where it makes sense, and always resolves to a visible outcome
(see [Reliability & failure handling](#reliability--failure-handling)).

## 2. Architecture and major components

| Component | File(s) | Role |
|---|---|---|
| Pipeline entry point | `app/trigger.py` | The single `start_run()` function the CLI and API both call — runs the ten automated stages in sequence |
| API | `app/main.py`, `app/security.py` | `POST /runs`, `GET /runs/{id}`, `GET /health` — API-key auth on every mutating endpoint |
| CLI | `prospectforge/cli.py` | Same entry point as the API, for manual/scripted triggering, review, and CRM sync |
| ICP / persona config | `prospectforge/icp/`, `prospectforge/persona/` | Who counts as a good-fit company and a good-fit contact — data, not code |
| Discovery → dedup | `prospectforge/discovery/`, `fit/`, `enrichment/`, `research/`, `people_discovery/`, `contact_enrichment/`, `dedup/` | The ten automated stages — see `docs/architecture.md` for the full module map |
| Qualification & prioritization | `prospectforge/qualification/`, `prospectforge/prioritization/` | The deterministic verdict + ranking logic (§ 15) |
| Human review | `prospectforge/review/` | Approve/reject a ranked prospect — deliberately outside the automated pipeline |
| CRM sync | `prospectforge/crm/` | Idempotent HubSpot sync, only for approved prospects |
| GDPR erasure | `prospectforge/privacy/` | Right-to-erasure for contact records |
| Reliability | `infra/retry.py`, `infra/circuit_breaker.py` | Every external call goes through retry/backoff + circuit-breaking |
| Observability | `infra/observability.py`, `app/logging.py` | Correlated structured logs, optional Sentry error tracking |
| Persistence | `app/orm.py`, `app/mappers.py`, `alembic/` | SQLAlchemy models + migrations, kept deliberately separate from the pydantic domain contracts in `prospectforge/models/` |

Every non-obvious decision behind these is written down in [`docs/adr/`](docs/adr/README.md)
as a numbered ADR, in the order it was actually decided. `docs/runbook.md` has the exact CLI
commands and SQL an operator runs to check on a run directly, instead of a dashboard.

## 3. Technology stack

Python 3.9, FastAPI, pydantic + pydantic-settings (every data contract and config value is
validated, not just typed), SQLAlchemy 2.0 + Alembic (Postgres, psycopg v3 driver), Anthropic's
Python SDK (research and optional rationale phrasing), Sentry SDK (optional error tracking),
httpx (every external HTTP call), pytest (445 tests), Docker + `docker-entrypoint.sh`,
deployed on Render.

## 4. Integrations currently included

| Role | Provider | Notes |
|---|---|---|
| Discovery / decision-maker search / contact match | [Apollo.io](https://apollo.io) | Free-tier search endpoints are gated (confirmed live - see ADR-003's addendum) - `csv`-backed fallback is the active default for these three |
| Account enrichment | [Apollo.io](https://apollo.io) | The one stage without a fallback - this specific endpoint is free-tier accessible, so a stand-in was never built |
| Company research | [Anthropic Claude](https://www.anthropic.com) (native web search) | No separate search API - see ADR-007 |
| Qualification rationale (optional, never the verdict) | [Anthropic Claude](https://www.anthropic.com) | Deterministic by default - see § 15 |
| CRM | [HubSpot](https://hubspot.com) | Free developer/sandbox tier. **Not yet live-verified against a real account** - the code path is tested against HubSpot's real API shape, but every live run so far has used a clearly labeled stand-in (see `docs/demo.md`) |
| Error tracking (optional) | [Sentry](https://sentry.io) | **SDK wiring only, not live-verified** - built and tested against a local capturing transport (no real Sentry account exists yet) |
| Database | [Postgres](https://postgresql.org) (via Render's managed Postgres in production, Docker locally) | |
| Hosting | [Render](https://render.com) | Chosen per the original project plan; the `Dockerfile`/`docker-entrypoint.sh` aren't Render-specific, so switching hosts is a config change, not a code change |

None of these are required to use this project's *code* — every stage except account
enrichment has a CSV-backed (or fully deterministic) default that needs zero credentials. See
§ 5 for what each one actually requires.

## 5. Provider/adapter architecture — how integrations get replaced

Core pipeline logic (`discovery/service.py`, `fit/`, `dedup/`, `qualification/engine.py`,
`prioritization/`, `review/service.py`, `crm/sync_service.py`, and every other `service.py`)
has **zero references to Apollo, HubSpot, or Anthropic by name** — checked directly, not
assumed (see [`docs/reusability.md`](docs/reusability.md) for the actual audit). Every
integration exists behind a port interface (`DiscoveryProvider`, `EnrichmentProvider`,
`ResearchProvider`, `PersonDiscoveryProvider`, `ContactEnrichmentProvider`, `RationaleProvider`,
`CRMAdapter` — all in each module's `interface.py`), using provider-neutral field names, not a
vendor's own object shape.

To swap a provider:

1. Write a new adapter file implementing the relevant interface (e.g.
   `prospectforge/crm/adapters/salesforce.py` implementing `CRMAdapter`).
2. Point that module's `get_default_*_provider()` factory at it for a new settings value.

Nothing else changes — not the pipeline orchestration, not the tests' structure, not the CLI.
This is the literal claim ADR-005 made before any code existed; `docs/reusability.md` is where
it was checked against the finished system rather than left as an assumption.

## 6. Configuration & credentials

Copy the template and fill in your own values — **never commit the result**:

```bash
cp .env.example .env
```

| Variable | Required? | What it's for |
|---|---|---|
| `PROSPECTFORGE_API_KEY` | **Required** | Authenticates `POST`/`GET /runs` — generate your own (`openssl rand -hex 32`), never a value from documentation or another deployment |
| `DATABASE_URL` | Required, has a working local default | Postgres connection string — matches `docker-compose.yml`'s local dev credentials by default |
| `APOLLO_API_KEY` | Required only for account enrichment | The one stage with no fallback — [app.apollo.io](https://app.apollo.io) → Settings → API (free tier) |
| `ANTHROPIC_API_KEY` | Optional | Research; optionally AI-phrased qualification rationale — [console.anthropic.com](https://console.anthropic.com) |
| `HUBSPOT_API_KEY` | Optional | CRM sync — a private app token from a HubSpot developer/sandbox account |
| `SENTRY_DSN` | Optional | Error tracking — [sentry.io](https://sentry.io) free tier |
| `DISCOVERY_PROVIDER` / `PEOPLE_DISCOVERY_PROVIDER` / `CONTACT_ENRICHMENT_PROVIDER` | Optional | `csv` (default) or `apollo` |
| `QUALIFICATION_RATIONALE_PROVIDER` | Optional | `deterministic` (default) or `anthropic` |

Full list with reasoning in [`.env.example`](.env.example).

**What is never committed**: `.env` is git-ignored and was never part of any commit in this
repository's history — verified directly, not assumed (see
[`docs/pre-public-audit.md`](docs/pre-public-audit.md) for the full audit performed before this
repo went public, including a scan of every tracked file's actual content for secret-shaped
strings). `.env.example` contains placeholders only.

## 7. Running it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Set PROSPECTFORGE_API_KEY to any long random string. Every other setting
# already has a working default.

docker compose up -d db     # local Postgres
alembic upgrade head        # apply the schema

uvicorn app.main:app --reload
```

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl -X POST http://localhost:8000/runs \
  -H "Authorization: Bearer <the value you put in .env>" \
  -H "Content-Type: application/json" \
  -d '{"icp_config_id": "saas-fictional-v1"}'
```

Without `APOLLO_API_KEY`, this runs discovery and pre-filtering against the bundled fictional
seed data, then stops cleanly at enrichment with a clear error — the app's fail-fast design
working as intended, not a bug (see `docs/runbook.md`). Add a free Apollo key to see all ten
automated stages complete.

Or via the CLI (every command listed at the top of
[`prospectforge/cli.py`](prospectforge/cli.py)):

```bash
python -m prospectforge.cli start-run --icp-config-id saas-fictional-v1
python -m prospectforge.cli review-queue
python -m prospectforge.cli approve --prospect-id <uuid>
python -m prospectforge.cli sync-to-crm      # requires HUBSPOT_API_KEY
python -m prospectforge.cli run-summary --run-id <uuid>
```

## 8. Docker & PostgreSQL

`docker-compose.yml` runs a local Postgres for development (`docker compose up -d db`) and, as
an optional profile, the app itself in its own container for a genuine test of the exact image
that would deploy (`docker compose --profile full up`) — day-to-day development is faster with
`uvicorn --reload` against the same Postgres service instead.

The `Dockerfile` builds on `python:3.9-slim` (matching the interpreter this project has run on
throughout); `psycopg-binary` ships its own compiled Postgres client library, so no extra
system packages are needed. `docker-entrypoint.sh` runs `alembic upgrade head` before starting
the server, on every container start — a no-op if the schema is already current, so it's safe
on restarts, not just first deploys.

## 9. Running the tests

```bash
python -m pytest                                          # SQLite, zero setup
DATABASE_URL=postgresql+psycopg://prospectforge:prospectforge@localhost:5432/prospectforge \
  python -m pytest                                         # against real Postgres (needs `docker compose up -d db` first)
```

(`python -m pytest`, not bare `pytest` — this project has no `pyproject.toml`/`pytest.ini`
setting `rootdir`, and `python -m` is what puts the project root on `sys.path` so `tests/
conftest.py`'s `from app import db` resolves — found as a real documentation bug during Step
24's own cold-read verification, not a hypothetical.)

445 tests: unit tests per module, integration tests running the whole pipeline end to end
(`tests/integration/test_full_pipeline.py`), and deliberate chaos/fault-injection tests —
killing the DB connection mid-write, malformed provider responses, forced LLM failures,
duplicate ingestion (`tests/integration/test_chaos.py`). See
[`docs/failure-scenario-coverage.md`](docs/failure-scenario-coverage.md) for every named
failure scenario mapped to the specific test that proves it.

## 10. Deployment

Dockerized, deployed to Render via [`render.yaml`](render.yaml) (a Blueprint - every secret
marked `sync: false` so Render prompts for it in its own dashboard rather than storing anything
in this repo). Full readiness audit, the migration-driver bug found on the actual first live
deploy attempt (Render's managed Postgres URL needed a driver-scheme fix - see
`app/config.py`'s `_normalize_postgres_driver`), and the how-to guide are in
[`docs/deployment.md`](docs/deployment.md). Live-verified: a real `POST /runs` against the
deployed instance processed real accounts through discovery and pre-filtering against the live
database (see `docs/deployment.md` for the full trace).

## 11. Security & privacy

- **No credentials in this repository, verified directly** — `.env` was never committed at any
  point in this repository's history; every tracked file's content was scanned for
  secret-shaped strings before this repo went public
  ([`docs/pre-public-audit.md`](docs/pre-public-audit.md)).
- **`POST`/`GET /runs` require a static API key**, compared with `hmac.compare_digest` (not
  `==`) to avoid leaking timing information about a partial match.
- **GDPR right-to-erasure is implemented, not just discussed**: `prospectforge/privacy/
  erasure.py` scrubs a contact's name/email/LinkedIn URL, and — the part easy to miss —
  redacts that contact's raw provider-response payloads and error-message audit rows too, not
  just the obvious column. A dedicated terminal contact status keeps an erased contact
  permanently out of every future re-enrichment retry.
- **PII never appears in application logs** — verified by grepping real formatted log output
  for a known email string, not just by code review (`tests/test_contact_enrichment_logging.py`,
  `tests/test_crm_sync_logging.py`). A real gap was found and fixed this way: CRM sync's
  failure log line could have echoed a submitted email back from a HubSpot validation error —
  closed in the same security pass that found it.
- **Interactive API docs are disabled in production** (`app/main.py`) — not a data-exposure
  fix (every endpoint already requires the API key), a general-principle one.
- Full findings, including the one dependency-vulnerability check performed and its result, in
  [`docs/security-privacy-review.md`](docs/security-privacy-review.md).

## 12. Reliability & failure handling

- **Every external call** goes through `infra/retry.py`'s `call_with_retry()`: classified as
  retryable or not, jittered exponential backoff (or a provider's own `Retry-After` hint when
  given), every attempt persisted to an audit table.
- **Circuit breaking** (`infra/circuit_breaker.py`): after repeated failures, a provider's
  circuit opens and further calls fail fast for a cooldown window, so one dead integration
  can't burn every remaining item's retry budget.
- **Per-item failure isolation**: one account's enrichment or research call failing never stops
  the batch - it's marked `*_failed` and retried automatically on the *next* run of that stage,
  not left stuck (a real orphaning bug here was found and fixed - see
  [`docs/failure-scenario-coverage.md`](docs/failure-scenario-coverage.md)).
- **Deliberate chaos testing**, not just unit tests: `tests/integration/test_chaos.py` kills
  the DB connection mid-write, injects malformed provider responses, forces research/LLM
  failures, and re-runs the whole pipeline twice against identical data to prove no duplicates
  - and confirms each produces the *designed* failure behavior, not a new one.

## 13. Observability

Structured JSON logs correlated by `run_id`/`account_id`/`contact_id` via contextvars (since
the project's foundation step) — the same correlation IDs are mirrored onto Sentry as tags when
`SENTRY_DSN` is configured. `run-summary` (CLI) answers "did the last run work, and if not,
where did it fail" from the `Run` row's own summary, without a dashboard.
`check-provider-health` computes a provider's recent failure rate from the retry audit trail
and logs (and, when Sentry is active, alerts on) a crossed threshold. **Sentry itself is SDK
wiring only** - built and tested against a local capturing transport per Sentry's own testing
pattern, not yet verified against a real Sentry account.

## 14. Human review / deterministic vs. AI

Qualification's verdict — qualified or not, at what confidence, for what reasons — comes
entirely from a deterministic engine (`qualification/engine.py`): fit tier + evidence + contact
completeness, zero AI involvement. An optional AI provider can *phrase* the rationale text more
naturally, but is structurally prevented from influencing the verdict — this was corrected
mid-project (Step 15) after initially defaulting to AI-phrased rationale, and both paths remain
on record for the same real account in [`docs/demo.md`](docs/demo.md) as proof the verdict is
identical either way.

No prospect reaches the CRM without an explicit human decision — `prospectforge/review/` is
deliberately outside the automated pipeline, gating CRM sync on a real approve/reject call, with
a required reason on rejection and a bulk-triage path for a growing queue.

## 15. Customizing for another ICP, client, or provider

- **A new ICP or persona** is one new YAML file (`prospectforge/icp/configs/`,
  `prospectforge/persona/configs/`) — no code change.
- **Switching a provider that's already built** (Apollo's search endpoints, once real access
  exists) is one environment variable.
- **A genuinely new provider** (a different discovery vendor, a different CRM) is one new
  adapter file — see § 5.
- **What still needs re-validating, not just re-configuring**: fit-tier thresholds and
  prioritization weights were reasoned through for one fictional scenario, not empirically
  tuned against real outcomes; the research/rationale prompt contracts were shaped by testing
  against Claude specifically and should be re-verified against a different LLM, not assumed
  to transfer. Full concrete list — reusable as-is, reusable with config only,
  provider-specific, needs re-verification — in [`docs/reusability.md`](docs/reusability.md).

## 16. Project scope & non-goals

This project deliberately stops at CRM sync — it does not attempt outreach, sequencing, or
reply handling. It's a learning project with fictional seed data standing in for a real ICP,
not a production SaaS product; see [`docs/adr/README.md`](docs/adr/README.md) for why each
major dependency was chosen and what would change to swap any of them.

## Documentation map

| Doc | What's in it |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Module boundaries, data flow, the ports/adapters pattern |
| [`docs/data-model.md`](docs/data-model.md) | Every record's fields, and the account/contact status state machines |
| [`docs/adr/`](docs/adr/README.md) | Every architectural decision, with alternatives considered and why it matters |
| [`docs/runbook.md`](docs/runbook.md) | Operational guide — what to check when something fails |
| [`docs/security-privacy-review.md`](docs/security-privacy-review.md) | GDPR posture, PII-in-logs audit, dependency check |
| [`docs/failure-scenario-coverage.md`](docs/failure-scenario-coverage.md) | Every named failure scenario mapped to the test that proves it |
| [`docs/pre-public-audit.md`](docs/pre-public-audit.md) | The audit performed before this repo went public |
| [`docs/deployment.md`](docs/deployment.md) | Deployment readiness audit and the live Render deployment |
| [`docs/reusability.md`](docs/reusability.md) | Honest audit of what a second client engagement would actually reuse |
| [`docs/demo.md`](docs/demo.md) | One real prospect's actual journey, ICP to CRM record — [published version](https://claude.ai/code/artifact/0cbd034e-ebfd-47db-94a0-e26d2ee22656) |

## Architecture decision log

Full table with "why it matters" for each in [`docs/adr/README.md`](docs/adr/README.md).

| # | Decision |
|---|---|
| [001](docs/adr/001-language-and-framework.md) | Python + FastAPI |
| [002](docs/adr/002-first-icp-scenario.md) | First ICP — fictional B2B SaaS scenario |
| [003](docs/adr/003-first-discovery-enrichment-provider.md) | Apollo.io (+ addendum: free-tier search gaps found live) |
| [004](docs/adr/004-first-crm.md) | HubSpot |
| [005](docs/adr/005-module-boundary-pattern.md) | Ports and adapters |
| [006](docs/adr/006-run-and-state-model.md) | Run and per-item state machine |
| [007](docs/adr/007-research-provider-design.md) | Claude's native web search, not a separate search API |
| [008](docs/adr/008-persona-matching-is-deterministic.md) | Persona matching is deterministic, not AI |
| [009](docs/adr/009-contact-pii-handling.md) | Contact PII handling (+ Step 20 erasure/logging addendum) |
| [010](docs/adr/010-dedup-merge-strategy.md) | Dedup merge strategy — no separate merge-log table |

## Project status & roadmap

All 26 steps of the original roadmap are complete — domain literacy and ICP methodology
through architecture, the full pipeline build, reliability hardening, a completed security/GDPR
review, deliberate chaos testing, observability wiring, a live Render deployment, full
documentation, an honest reusability audit, and an end-to-end demonstration traced through real
data. "Complete" here means built, tested, and — where a real account exists (Apollo,
Anthropic) — live-verified; where one doesn't yet (HubSpot, Sentry), that's stated explicitly
above rather than implied. See [`docs/demo.md`](docs/demo.md) for the single clearest evidence
trail of what actually works together.

## License

[MIT](LICENSE) — free to use, modify, and adapt to your own business.
