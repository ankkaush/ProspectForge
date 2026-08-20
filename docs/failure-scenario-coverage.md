# Failure Scenario Coverage (Step 21)

Every failure scenario named in the roadmap (Steps 1-20), mapped to the test(s) that prove it.
Compiled by re-reading every step's "Failure scenarios" field and checking actual test
coverage, not assuming it existed - this audit itself found one real gap (Step 5), fixed
below.

| Step | Scenario | Proven by |
|---|---|---|
| 1-2 | Conceptual only (loose vocabulary, over/under-admitting ICP) | N/A - no code, not testable |
| 3 | Provider/CRM swap should touch one adapter file, not pipeline logic | Structural, by construction: `discovery_provider`/`people_discovery_provider`/`contact_enrichment_provider`/`qualification_rationale_provider` config switches (csv↔apollo, deterministic↔anthropic) touch zero pipeline code across Steps 7-18 |
| 4 | A field must be representable as "unknown," distinct from "known empty" | `Optional[...] = None` throughout every Step 4 model; exercised concretely by `EnrichmentResult(found=False)` vs. a populated result, and `FitTier.INSUFFICIENT_DATA` vs. `REJECTED` |
| 5 | Missing required env var fails fast with a clear error | **Gap found and fixed this step** - `tests/test_config.py` (new) |
| 6 | ICP config requiring a post-enrichment field pre-enrichment fails at load | `tests/test_icp.py::test_wrong_phase_for_pre_enrichment_field_is_rejected` |
| 7 | Apollo rate-limited (retryable); missing domain; quota exhausted; whole call fails → run-level failure | `test_discovery_apollo_provider.py::test_server_and_rate_limit_errors_are_retryable`, `::test_skips_organization_with_no_domain_at_all`; run-level failure via `test_retry.py`'s exhaustion tests + `app/trigger.py`'s broad except (also re-proven live in `tests/integration/test_chaos.py::test_a_db_write_failure_mid_run_produces_a_clear_failed_run_not_a_hang`) |
| 8 | Over-aggressive filtering drops good accounts silently; needs per-rule audit trail | `test_fit_prefilter.py` - every rejection asserts the specific reason string (e.g. `"Regulatory" in result.reasons[0]`) |
| 9 | Provider has no data → "unknown," not a fit failure; failed call isolated per-account | `test_enrichment_service.py` (no-data + isolation), Step 19's retry fix re-proven in `::test_an_account_stuck_at_enrichment_failed_gets_retried_and_can_succeed` |
| 10 | Partial enrichment data degrades gracefully, doesn't throw | `test_fit_evaluator.py` |
| 11 | LLM timeout, invalid JSON, hallucinated fact, zero results - each has a defined fallback; isolated per-account | `test_research_extractor.py` (parsing/hallucination), `test_research_anthropic_provider.py` (timeout/JSON retry), `test_research_service.py` (isolation + Step 19's `::test_an_account_stuck_at_research_failed_gets_retried_and_can_succeed`) |
| 12 | Stale title data; multiple valid contacts surfaced, not silently narrowed; failed call isolated | `test_people_discovery_csv_provider.py`, `test_people_discovery_service.py` |
| 13 | Low-deliverability email stored with that confidence, never silently promoted; failed call isolated per-contact | `test_contact_enrichment_csv_provider.py`; isolation + Step 19's fix in `test_contact_enrichment_service.py::test_a_contact_stuck_at_enrichment_failed_gets_retried_and_can_succeed` |
| 14 | Over-aggressive fuzzy matching merges distinct companies; needs conservative threshold + audit trail | `test_dedup_matchers.py` (threshold), `test_dedup_service.py` (merge audit trail / `merges` summary) |
| 15 | Rationale hallucinates a fact not in evidence | `test_qualification_rationale.py` (evidence-id cross-check), live-verified at Step 15 against a real Anthropic credit-exhaustion event |
| 16 | A single dominant weight drowns out the others | `test_prioritization_scorer.py::test_no_default_weight_exceeds_half`, `::test_evidence_alone_can_flip_the_ranking_of_two_otherwise_tied_prospects` |
| 17 | Review queue grows unbounded, no bulk-triage path | `test_review_service.py::test_bulk_reject_pending_only_touches_pending_records` |
| 18 | HubSpot down/rate-limited (isolated per-record); token expired; conflicting existing data - who wins | `test_crm_hubspot_adapter.py` (retryable/non-retryable classification, Retry-After), `::test_matched_company_and_contact_are_not_overwritten` (who wins: HubSpot does), `test_crm_sync_service.py::test_adapter_failure_is_isolated_and_does_not_mark_synced` |
| 19 | Provider down, rate-limited, slow, flaky, partial response - systematically | `test_retry.py`, `test_circuit_breaker.py`; re-proven live end-to-end in `tests/integration/test_chaos.py::test_malformed_provider_response_on_one_account_does_not_stop_the_others` and `::test_llm_json_corruption_does_not_crash_the_run_and_the_account_is_still_reachable_later` |
| 20 | A stray log/print leaks an email or API key | `test_contact_enrichment_logging.py`, `test_crm_sync_logging.py` (this step's own fix) |

## What Step 21 added beyond the audit

The table above is mostly *unit*-level proof, one stage or adapter at a time - already solid,
but it doesn't prove the failure modes still behave correctly once they're composing inside a
real, full run. `tests/integration/` adds that layer:

- **`test_full_pipeline.py`** - ICP through CRM sync in one test, including the two
  human-gated stages (review, CRM sync) that `start_run()` deliberately excludes and so had
  never been exercised together with the automated ten before.
- **`test_chaos.py`** - the roadmap's four named chaos scenarios, run against the real
  pipeline rather than a mocked unit:
  1. **Kill the DB mid-write** - a `session.flush()` that raises `OperationalError`
     partway through discovery. Result: `RunStatus.FAILED`, a named `failed_stage`, no hang,
     no corrupt partial write.
  2. **Malformed provider responses** - one account's enrichment call fails outright, the
     other succeeds, in the same run. Result: `PARTIAL_SUCCESS`, per-item isolation holds, the
     pipeline still runs every later stage for the account that succeeded.
  3. **Forced LLM JSON corruption** - research fails for every account in one run. Result:
     `PARTIAL_SUCCESS`, zero accounts reach qualification, and Step 19's orphan-retry fix
     picks every one of them back up on a follow-up call.
  4. **Duplicate ingestion** - the *whole pipeline*, not just discovery, run twice against
     identical source data. Result: zero duplicate accounts; the second run's discovery
     summary correctly reports 0 persisted / 2 skipped-as-duplicate.

## Exit criteria assessment

*"You can point to a test for every failure scenario named earlier in this roadmap."* Every
scenario in the table above has one. One real gap (Step 5's config validation) was found by
doing this audit rather than assuming coverage existed, and is now fixed.
