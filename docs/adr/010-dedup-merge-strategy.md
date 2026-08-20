# ADR-010: Dedup Merge Strategy — No Separate Merge-Log Table

## Context
Step 14 needs an "audit trail of every merge decision" (the roadmap's explicit failure-scenario
requirement, guarding against over-aggressive fuzzy matching silently combining two distinct
companies). The question is where that trail lives.

## Decision
No new `merge_log` table. Every merge is logged via the project's existing structured-logging
mechanism (the same JSON logs every other stage already writes through) and returned in the
run's summary as a list of `{survivor, merged, reason}` entries - the identical pattern every
other stage in this project uses to report what it did (`discovery`, `enrichment`,
`fit_evaluation`, etc. summaries).

## Alternatives considered
- **A dedicated `merge_log` table** - rejected: it's schema complexity for something the
  existing audit mechanisms (structured logs + `ExternalCallAttempt`-style tables for the
  stages that actually call external providers) already cover reasonably well for a step that
  runs purely in-process, with no external call to log an *attempt* for in the first place.
  Revisit if merges need to be queried historically after the log stream has rotated away -
  not a need this project has yet.

## Merge semantics (recorded here since there's no dedicated schema to encode them in)
- **Survivor selection**: the account/contact further along its state machine wins (more
  pipeline stages have vetted it); ties broken by which record is older. See
  `dedup/service.py`'s `STATUS_RANK` and `_EMAIL_CONFIDENCE_RANK`.
- **Field merge, not row replacement**: the survivor keeps every non-null value it already
  has; only fields the survivor is missing get filled in from the loser. The loser never
  overwrites a value the survivor already has - this is what "preserves the highest-confidence
  value per field, not just the newest" means in practice.
- **Every child row is re-pointed before the loser is deleted** - contacts, fit results,
  evidence, provider records, and external call attempts referencing the loser account (or
  contact) are updated to reference the survivor first. Nothing is silently orphaned.

## Consequences
- Merge history is greppable in logs and visible in the run's summary, but not queryable via
  SQL after the fact the way a table would be. Accepted for this project's scale and current
  needs.
- The conservative similarity thresholds (`ACCOUNT_NAME_SIMILARITY_THRESHOLD = 0.90`,
  `CONTACT_NAME_SIMILARITY_THRESHOLD = 0.92`) are a deliberate choice, not a default - tuned to
  catch "Northstar Metrics" / "Northstar Metrics Inc." while rejecting "Northstar Metrics" /
  "Northstar Analytics". Revisit only with evidence a real near-duplicate is being missed or a
  real false-positive is happening, not preemptively.
