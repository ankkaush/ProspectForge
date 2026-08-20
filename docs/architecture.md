# ProspectForge — Architecture Overview

See `docs/adr/` for the full reasoning behind each decision summarized here.

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

- **Core pipeline** (never imports a vendor SDK): `discovery/service.py`,
  `fit/prefilter.py`, `fit/evaluator.py`, `research/service.py`,
  `people_discovery/service.py`, `dedup/service.py`, `qualification/engine.py`,
  `prioritization/scorer.py`, `review/service.py`, `crm/sync_service.py`.
- **Adapters** (all vendor-specific code lives here, isolated):
  `discovery/providers/apollo.py`, `enrichment/providers/apollo.py`,
  `contact_enrichment/providers/apollo.py`, `crm/adapters/hubspot.py`, LLM client adapter.
- **Internal contracts** (Step 4, shared language between everything above):
  `Account`, `Contact`, `Evidence`, `ProviderRecord`, `FitResult`, `QualificationResult`,
  `ProspectRecord`.

## Status

This document reflects decisions made through **Step 3** of the project roadmap. No
application code has been written yet — Step 4 (internal data contracts) and Step 5
(project foundation/scaffolding) come next.
