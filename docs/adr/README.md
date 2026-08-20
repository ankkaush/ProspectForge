# Architecture Decision Records

Each ADR captures a decision, the alternatives considered, and the consequences - written at
the time the decision was made, not reconstructed afterward. `docs/architecture.md` summarizes
the highlights; this is the durable, detailed record.

| ADR | Decision |
|---|---|
| [001](001-language-and-framework.md) | Language & framework — Python + FastAPI |
| [002](002-first-icp-scenario.md) | First ICP — fictional B2B SaaS scenario |
| [003](003-first-discovery-enrichment-provider.md) | First discovery/enrichment provider — Apollo.io (+ addendum: free-tier search access gaps found live) |
| [004](004-first-crm.md) | First CRM — HubSpot |
| [005](005-module-boundary-pattern.md) | Module boundary pattern — ports and adapters |
| [006](006-run-and-state-model.md) | Run and per-item state machine |
| [007](007-research-provider-design.md) | Research provider design — Claude's native web search, not a separate search API |
| [008](008-persona-matching-is-deterministic.md) | Persona matching is deterministic keyword matching, not AI |
| [009](009-contact-pii-handling.md) | Contact PII handling (+ Step 20 addendum: erasure mechanism, logging discipline gap found and fixed) |
| [010](010-dedup-merge-strategy.md) | Dedup merge strategy — no separate merge-log table |

Two later decisions of similar weight didn't get their own ADR number - they're documented
inline where they were made, since each is a single, self-contained code change rather than a
project-wide policy:

- **Qualification rationale defaults to deterministic, not AI** (Step 15's correction) -
  see `prospectforge/qualification/service.py`'s module docstring.
- **`ExternalCallAttempt.run_id` made nullable** (Step 18, first schema migration since the
  initial one) - see `infra/retry.py`'s docstring and migration `6d9c4e4f2171`.
