# Data Model Reference

Every model exists in two forms, deliberately kept separate (see ADR-005 and `app/orm.py`'s
docstring): a **pydantic contract** (`prospectforge/models/`) that business logic reads and
writes, and a **SQLAlchemy ORM model** (`app/orm.py`) that's what's actually stored. `app/
mappers.py` converts between them where a service needs to. This document describes the
contract - field names, types, and purpose match the ORM columns one-for-one unless noted.

## Account

A company, persistent across pipeline runs. `prospectforge/models/account.py`.

| Field | Type | Notes |
|---|---|---|
| `domain` | `str` | The dedup key - see "Duplicates" below |
| `name`, `industry`, `employee_count`, `geography` | | Firmographic, available pre-enrichment |
| `tech_stack`, `funding_stage`, `growth_signal` | | `None` until Step 9 enrichment runs - "unknown," never a guessed default |
| `status` | `AccountStatus` | See the state machine below |
| `fit_tier` | `FitTier \| None` | Set by fit evaluation (prefilter or full pass) |
| `discovered_in_run_id` | `uuid \| None` | Provenance, not ownership - the account outlives the run |

**`AccountStatus` state machine** (`ACCOUNT_STATUS_TRANSITIONS` in `enums.py`, enforced by
`Account.transition_to()`, which raises `IllegalStatusTransition` on an illegal move):

```
raw ──► advanced ──► enriched ──► fit_evaluated ──┬─► researched ──┬─► qualified ──► reviewed ──┬─► synced
 │           │                        │            │                │                            │
 └► rejected_early    enrichment_failed┘            └► research_failed  └► not_qualified           └► not_qualified
                       (retries via     └► rejected  (retries via
                        enriched)                     researched)
```

Every stage queries for its own incoming status **and** its own `_failed` retry status (fixed
at Step 19 - see `docs/failure-scenario-coverage.md`) - a `_failed` status is never a dead end.

## Contact

A specific person at an Account. `prospectforge/models/contact.py`.

| Field | Type | Notes |
|---|---|---|
| `account_id` | `uuid` | |
| `name`, `title`, `seniority`, `department` | | |
| `email`, `email_confidence` | | `email_confidence` is `None` until contact enrichment runs - never silently promoted to `"verified"` |
| `linkedin_url` | | |
| `status` | `ContactStatus` | `discovered → enriched \| enrichment_failed → erased` (no enforced transition table - simpler state space than Account) |
| `erased_at` | `str \| None` | Set by `privacy/erasure.py` (Step 20/GDPR) - terminal, permanently excluded from every retry query |

## Evidence

A single sourced, confidence-tagged claim about an Account, from company research (Step 11).
`prospectforge/models/evidence.py`.

| Field | Type | Notes |
|---|---|---|
| `account_id`, `contact_id` | | `contact_id` optional |
| `claim` | `str` | e.g. "Posted 4 open engineering roles in the last 30 days" |
| `source_type` | `EvidenceSourceType` | `provider_api` \| `ai_inferred` \| `manual` |
| `source_url` | `str \| None` | Required in practice for `ai_inferred` claims (enforced by the extractor, not the schema) |
| `confidence` | `ConfidenceLevel` | `low` \| `medium` \| `high` |

## ProviderRecord

The raw response from a provider call, kept verbatim for audit/replay - distinct from
`Evidence`, which is an AI-extracted *claim*, not a structured API response.
`prospectforge/models/provider_record.py`.

| Field | Type | Notes |
|---|---|---|
| `account_id`, `contact_id` | | Either or both, depending on `operation` |
| `provider` | `str` | Plain string (`"apollo"`, not an enum) - adding a provider never edits this model |
| `operation` | `str` | e.g. `"discovery"`, `"account_enrichment"`, `"contact_enrichment"` |
| `payload` | `dict` | The provider's response, verbatim - redacted wholesale on contact erasure (Step 20) |

## FitResult

The outcome of evaluating an Account against the ICP, at one of two passes.
`prospectforge/models/fit.py`.

| Field | Type | Notes |
|---|---|---|
| `account_id` | | |
| `pass_type` | `FitPassType` | `prefilter` (Step 8, cheap) or `full` (Step 10, post-enrichment) |
| `tier` | `FitTier` | `tier_1` \| `tier_2` \| `tier_3` \| `rejected` \| `insufficient_data` |
| `reasons` | `list[str]` | One entry per criterion that drove the tier - the audit trail for "why this tier" |

## QualificationResult

Fit + evidence + contact completeness synthesized into an explainable verdict (Step 15, fully
deterministic - see `qualification/engine.py`). `prospectforge/models/qualification.py`.

| Field | Type | Notes |
|---|---|---|
| `account_id`, `contact_id` | | One result **per contact**, not per account - a multi-contact account gets independently-scored results |
| `status` | `QualificationStatus` | `qualified` \| `not_qualified` |
| `reasons` | `list[str]` | |
| `confidence` | `float` (0-1) | |
| `evidence_ids` | `list[uuid]` | Every claim in `rationale_text` must cite one of these or it's dropped |
| `rationale_text` | `str \| None` | Deterministic by default (Step 15's correction); optionally AI-phrased - never AI-decided |

## ProspectRecord

The CRM-bound composite - the finished, human-reviewed package Step 18 actually syncs.
References other records by id rather than re-embedding them. `prospectforge/models/
prospect.py`.

| Field | Type | Notes |
|---|---|---|
| `account_id`, `contact_id`, `qualification_result_id` | | |
| `priority_score`, `priority_rank` | | Set by prioritization (Step 16); globally re-ranked every run |
| `review_decision` | `ReviewDecision` | `pending` → `approved` \| `rejected` (Step 17) |
| `review_reason` | `str \| None` | Required on rejection |
| `crm_object_id`, `synced_at` | | Set once CRM sync succeeds (Step 18) - only `approved` records are ever attempted |

## Run

One row per pipeline execution. `prospectforge/models/run.py`.

| Field | Type | Notes |
|---|---|---|
| `icp_config_id` | `str` | |
| `status` | `RunStatus` | `pending` → `running` → `completed` \| `partial_success` \| `failed` |
| `summary` | `dict` | Per-stage counts - what `run-summary` (CLI) reads to answer "did it work, and if not, where" |

## ExternalCallAttempt

One row per attempt at a retryable external call - the audit trail every reliability feature
(Step 19's circuit breaker, Step 22's failure-rate alerting) reads from.
`prospectforge/models/external_call.py`.

| Field | Type | Notes |
|---|---|---|
| `run_id` | `uuid \| None` | Nullable as of Step 18 - CRM sync isn't scoped to a Run |
| `account_id`, `contact_id` | | |
| `provider`, `operation` | `str` | |
| `attempt_number` | `int` | `0` means the circuit was open - no real attempt made |
| `status` | `CallStatus` | `success` \| `failed_retryable` \| `failed_non_retryable` \| `failed_exhausted` \| `circuit_open` |
| `error_message` | `str \| None` | Redacted on contact erasure if it references that contact |

## Duplicates

`Account.domain` is the dedup key at two separate points: ingestion-time (Step 7 - a
discovery page returning the same domain twice, or a domain already known from an earlier run,
is skipped before persisting) and Step 14's dedup pass (fuzzy-matches near-duplicate accounts
that slipped through with slightly different domains/names, merges field-by-field, never
overwriting a survivor's already-known values).
