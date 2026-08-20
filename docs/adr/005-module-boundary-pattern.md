# ADR-005: Module Boundary Pattern — Ports and Adapters

## Context
ProspectForge's core requirement (per project brief) is that no external integration —
discovery/enrichment provider, LLM, or CRM — may become baked into the core pipeline logic.
The system must support swapping any one of these without rewriting business logic, since
reusability across future ICPs/providers/CRMs is an explicit project goal.

## Decision
Use a ports-and-adapters (hexagonal) architecture. Core pipeline modules (discovery
orchestration, fit scoring, qualification, prioritization, dedup) depend only on interfaces
("ports") that we define ourselves — never on a vendor SDK or API client directly. Each
external integration is implemented as a separate "adapter" module conforming to its port.

Ports defined so far (implementations noted; only one implementation per port is being
built now — see ADR-003, ADR-004):

| Port | First adapter |
|---|---|
| `DiscoveryProvider` | `ApolloDiscoveryProvider` |
| `EnrichmentProvider` (account) | `ApolloEnrichmentProvider` |
| `EnrichmentProvider` (contact) | `ApolloContactEnrichmentProvider` |
| `ResearchSource` | web search/fetch adapter (TBD at Step 11) |
| `LLMClient` | Claude API adapter |
| `CRMAdapter` | `HubSpotAdapter` |

## Alternatives considered
- **Direct vendor SDK calls throughout the pipeline** — simpler and faster to write
  initially, but ties core logic to Apollo/HubSpot's specific data shapes and failure modes.
  Rejected: it directly violates the project's stated provider-independence requirement and
  cannot be retrofitted cheaply once pipeline and vendor code are entangled.
- **A generic plugin/registry framework for providers** — rejected as unnecessary complexity
  for a single implementation per port; nothing here requires runtime plugin discovery, only
  a fixed interface and one concrete class per port, wired via configuration.

## Consequences
- Every provider/CRM adapter maps its vendor's response shape onto our internal contracts
  (Step 4: `Account`, `Contact`, `Evidence`, etc.) at the adapter boundary — vendor field
  names never leak past the adapter.
- Test suites (Step 21) can exercise core pipeline logic against fake/mock implementations
  of each port, without live network calls.
- **Boundary health check, used throughout the project**: if swapping a provider or CRM ever
  requires touching pipeline logic (not just the adapter and its wiring), the boundary was
  drawn in the wrong place and should be revisited.
