# Architecture Decision Records

Each ADR captures a decision, the alternatives considered, and the consequences - written at
the time the decision was made, not reconstructed afterward. `docs/architecture.md` summarizes
the highlights; this is the durable, detailed record.

| ADR | Decision | Why it matters |
|---|---|---|
| [001](001-language-and-framework.md) | Language & framework — Python + FastAPI | The foundation every later decision builds on - pydantic gives every data contract in the pipeline structural validation for free. |
| [002](002-first-icp-scenario.md) | First ICP — fictional B2B SaaS scenario | Fixes what "good fit" means concretely, so fit/qualification logic has a real target to be tested against instead of an abstract one. |
| [003](003-first-discovery-enrichment-provider.md) | First discovery/enrichment provider — Apollo.io (+ addendum: free-tier search access gaps found live) | The addendum is the important part: a live check found Apollo's free tier blocks every *search* endpoint but not enrichment - directly explains why discovery defaults to `csv` while enrichment always calls the real API. |
| [004](004-first-crm.md) | First CRM — HubSpot | Sets the CRM sync contract (Company/Contact/Note) every `CRMAdapter` implementation has to satisfy. |
| [005](005-module-boundary-pattern.md) | Module boundary pattern — ports and adapters | The rule that makes every other integration swappable: core pipeline logic never imports a vendor SDK directly - checked, not assumed, in `docs/reusability.md`. |
| [006](006-run-and-state-model.md) | Run and per-item state machine | What makes crash recovery a plain status query instead of a special feature - every stage's "what's left to do" question has the same answer shape. |
| [007](007-research-provider-design.md) | Research provider design — Claude's native web search, not a separate search API | Avoids building and maintaining a second search integration solely for research, when Claude's own tool already does the job. |
| [008](008-persona-matching-is-deterministic.md) | Persona matching is deterministic keyword matching, not AI | A concrete instance of the project's broader "AI never decides what doesn't need it" principle, applied one step earlier than qualification. |
| [009](009-contact-pii-handling.md) | Contact PII handling (+ Step 20 addendum: erasure mechanism, logging discipline gap found and fixed) | The GDPR posture for the one place this project handles real personal data - the addendum documents a real gap (CRM sync's logging) found and closed during the security review. |
| [010](010-dedup-merge-strategy.md) | Dedup merge strategy — no separate merge-log table | Why a fuzzy-matched duplicate gets merged conservatively, field-by-field, with the merge itself recorded in the run summary rather than a whole new audit table. |

Two later decisions of similar weight didn't get their own ADR number - they're documented
inline where they were made, since each is a single, self-contained code change rather than a
project-wide policy:

- **Qualification rationale defaults to deterministic, not AI** (Step 15's correction) -
  see `prospectforge/qualification/service.py`'s module docstring. Why it matters: the AI
  phrasing layer is fully optional and structurally cannot influence the qualification verdict
  - the deterministic engine decides first, unconditionally.
- **`ExternalCallAttempt.run_id` made nullable** (Step 18, first schema migration since the
  initial one) - see `infra/retry.py`'s docstring and migration `6d9c4e4f2171`. Why it matters:
  CRM sync isn't scoped to any one pipeline run, so its audit trail needed a run-independent
  path without weakening the constraint for every stage that *is* run-scoped.
