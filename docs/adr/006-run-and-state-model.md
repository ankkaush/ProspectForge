# ADR-006: Run and Per-Item State Machine

## Context
The pre-Step-4 technical audit found that the original data contracts
(Account, Contact, Evidence, ProviderRecord, QualificationResult,
ProspectRecord) had no explicit model for "which pipeline execution does
this data belong to" or "exactly how far did this specific account get
before a crash." Without these, resuming a partially-completed run has no
concrete mechanism.

## Decision
Add two models: `Run` (one row per pipeline execution, with a coarse
run-level status and a summary of counts per stage) and
`ExternalCallAttempt` (one row per attempt at a retryable external call,
recording provider, operation, attempt number, and outcome).

Give `Account` and `Contact` an explicit status enum representing their
position in the pipeline (`AccountStatus`: raw → advanced/rejected_early →
enriched/enrichment_failed → fit_evaluated/rejected → researched →
qualified/not_qualified → reviewed → synced), with a fixed table of legal
transitions (`ACCOUNT_STATUS_TRANSITIONS`) enforced by an
`Account.transition_to()` method that raises `IllegalStatusTransition`
rather than silently allowing an out-of-order state change.

## Alternatives considered
- **No explicit state machine — infer progress from which related rows
  exist** (e.g. "has a FitResult" implies "fit was evaluated"). Rejected:
  this makes every stage's "what's left to process" query an implicit join
  across multiple tables instead of a single indexed status filter, and
  makes illegal orderings (e.g. qualifying an account with no FitResult)
  possible to construct by accident.
- **A generic workflow/state-machine library** — rejected as unnecessary
  complexity for a fixed, small set of states known in advance; a plain
  enum plus a transition table is sufficient and easier to reason about.

## Consequences
- Every pipeline stage can be described uniformly as "read rows at status
  X for this run, process, write status Y" — this is the mechanism that
  makes crash recovery a plain query rather than a special feature (see
  docs/architecture.md and the technical audit for the worked example).
- `Account`/`Contact` now carry pipeline state directly, rather than that
  state being reconstructed from the presence/absence of other rows.
- `ExternalCallAttempt` gives steps 19-21 concrete data to verify retry and
  backoff behavior against, instead of only trusting log output.
