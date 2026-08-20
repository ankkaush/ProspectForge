# ProspectForge — Architecture Overview

See `docs/adr/` (indexed in `docs/adr/README.md`) for the full reasoning behind each decision
summarized here. See `docs/data-model.md` for every model's fields, and `docs/runbook.md` for
what to check when something goes wrong.

## Summary of foundational decisions

- **Language/framework**: Python + FastAPI, pydantic for all data contracts and structured
  validation. (ADR-001)
- **First ICP**: fictional mid-market B2B SaaS scenario, values constructed after learning
  ICP methodology (Step 2), not invented ahead of it. (ADR-002)
- **First discovery/enrichment provider**: Apollo.io, free tier, behind
  `DiscoveryProvider` / `EnrichmentProvider` interfaces. (ADR-003)
- **First CRM**: HubSpot, free developer tier, behind a `CRMAdapter` interface. (ADR-004)
- **Module boundary pattern**: ports and adapters — core pipeline logic never depends on a
  vendor SDK directly. (ADR-005)
- **Run/state model**: every pipeline execution is a `Run` row; `Account`/`Contact` carry an
  explicit status enum with a fixed legal-transition table, so "what's left to process" is
  always a plain status query, and crash recovery needs no special mechanism. (ADR-006)
- **Dedup merge strategy**: two-tier matching (exact key, then conservative fuzzy fallback),
  field-by-field merge that never overwrites a survivor's known values. (ADR-010)

## Module map

```
                          ┌───────────────────────────────┐
                          │   ICP Config (Step 6)          │
                          │   data, not code                │
                          └───────────────┬─────────────────┘
                                           │
 ┌──────────────┐   DiscoveryProvider  ┌───▼────────────┐
 │ Apollo        │◄─────────interface──┤ Discovery       │  (Step 7)
 │ Discovery      │                     │ service         │
 │ adapter        │                     └───┬────────────┘
 └──────────────┘                           │ raw Account
                                             ▼
                                   ┌───────────────────┐
                                   │ Fit prefilter      │ (Step 8, cheap/pre-enrichment)
                                   └───┬────────────────┘
                                       │ advanced Account
 ┌──────────────┐  EnrichmentProvider ┌▼───────────────┐
 │ Apollo        │◄────────interface──┤ Enrichment      │ (Step 9)
 │ Enrichment     │                    │ service         │
 │ adapter        │                    └───┬────────────┘
 └──────────────┘                          │ enriched Account
                                            ▼
                                  ┌────────────────────┐
                                  │ Full fit evaluator  │ (Step 10)
                                  └───┬─────────────────┘
                                      │ tiered Account
                    ┌─────────────────┼─────────────────────┐
                    ▼                                        ▼
        ┌───────────────────┐                    ┌───────────────────────┐
 LLMClient│ Company research   │ (Step 11)          │ Decision-maker         │ (Step 12)
 interface│ service            │                    │ discovery service      │◄── DiscoveryProvider
        └─────────┬──────────┘                    └───────────┬────────────┘  (people search)
                  │ Evidence                                    │ candidate Contact
                  │                                              ▼
                  │                                   ┌────────────────────────┐
                  │                          EnrichmentProvider│ Contact          │ (Step 13)
                  │                          interface ◄───────┤ enrichment       │
                  │                                             │ service          │
                  │                                             └────────┬─────────┘
                  └───────────────────────┬─────────────────────────────┘
                                           ▼
                                ┌────────────────────┐
                                │ Validation & dedup   │ (Step 14)
                                └───┬────────────────┘
                                    ▼
                          ┌────────────────────┐
                   LLMClient│ Qualification       │ (Step 15)
                   interface◄┤ engine             │
                          └───┬────────────────┘
                              ▼
                    ┌────────────────────┐
                    │ Prioritization       │ (Step 16)
                    └───┬────────────────┘
                        ▼
              ┌────────────────────┐
              │ Human review         │ (Step 17)
              └───┬────────────────┘
                  │ approved ProspectRecord
                  ▼
 ┌──────────────┐  CRMAdapter    ┌────────────────────┐
 │ HubSpot        │◄──interface──┤ CRM sync service     │ (Step 18)
 │ adapter         │              └────────────────────┘
 └──────────────┘
```

## The boundary health check

Used throughout the project as a standing test of whether a module boundary is drawn
correctly: **if swapping a provider, CRM, or LLM ever requires editing pipeline logic — not
just the adapter and its wiring — the boundary is wrong and should be revisited.** This
applies to every row in the module map above, not just discovery.

## What lives on which side of a port

- **Core pipeline** (never imports a vendor SDK): `discovery/service.py`, `fit/prefilter.py`,
  `fit/evaluator.py`, `enrichment/service.py`, `research/service.py`,
  `people_discovery/service.py`, `contact_enrichment/service.py`, `dedup/service.py`,
  `dedup/matchers.py`, `qualification/engine.py` (fully deterministic - see below),
  `qualification/service.py`, `prioritization/scorer.py`, `prioritization/service.py`,
  `review/service.py`, `crm/sync_service.py`.
- **Adapters** (all vendor-specific code lives here, isolated): `discovery/providers/apollo.py`,
  `enrichment/providers/apollo.py`, `people_discovery/providers/apollo.py`,
  `contact_enrichment/providers/apollo.py`, `research/providers/anthropic_web_search.py`,
  `qualification/providers/anthropic_rationale.py`, `crm/adapters/hubspot.py`. Every one of
  these has a `csv`-backed (or, for qualification's rationale, `deterministic`) counterpart
  used as the active default - see ADR-003's addendum for why (Apollo's free plan blocks every
  search-type endpoint) and Step 15's correction for why qualification's rationale defaults to
  no AI call at all.
- **Internal contracts** (Step 4, shared language between everything above): `Account`,
  `Contact`, `Evidence`, `ProviderRecord`, `FitResult`, `QualificationResult`,
  `ProspectRecord`, `Run`, `ExternalCallAttempt` - full field reference in
  `docs/data-model.md`.

## Reliability, security, and observability (Steps 19-22)

Layered on top of the pipeline above, not a separate system:

- **`infra/retry.py`** - every external call in the system goes through `call_with_retry()`:
  classifies failures as `RetryableError`/`NonRetryableError`, applies jittered exponential
  backoff (or a provider's own `Retry-After` hint when given), and persists every attempt as an
  `ExternalCallAttempt` row.
- **`infra/circuit_breaker.py`** - after repeated failures, a provider's circuit opens and
  further calls fail fast (no network attempt) for a cooldown window, so one dead provider
  can't burn every remaining item's retry budget.
- **`infra/observability.py`** - optional Sentry error tracking (disabled without
  `SENTRY_DSN`, same pattern as every other integration) wired through the logging module
  rather than scattered capture calls, plus a provider failure-rate check against the
  `ExternalCallAttempt` audit trail.
- **`prospectforge/privacy/erasure.py`** - GDPR right-to-erasure for `Contact` records,
  redacting personal fields everywhere they're stored (not just the obvious column - see
  `docs/security-privacy-review.md`).
- **`app/logging.py`** - structured JSON logs correlated by `run_id`/`account_id`/`contact_id`
  via contextvars, since Step 5; the same correlation ids are mirrored onto Sentry as tags.

## Two stages deliberately outside `start_run()`

`app/trigger.py`'s `start_run()` runs ten stages automatically: discovery through
prioritization. **Review** (Step 17) and **CRM sync** (Step 18) are not part of it - both
depend on a human decision made on their own schedule, not on any one pipeline run, so both are
separate, independently-triggered CLI/service entry points that query by record state
(`ProspectRecord.review_decision`, `synced_at`) rather than by which run produced the data.

## Status

This document reflects the system as it stood after **Step 23** (deployed to Render). The full
26-step roadmap, amendments, and the technical audit that shaped Steps 4-5 are preserved in the
published roadmap artifact referenced at the start of this project; `docs/adr/` is the durable,
in-repo record of every decision.
