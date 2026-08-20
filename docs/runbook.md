# Runbook: What to Check When Something's Wrong

## "A run failed" - start here

```bash
python -m prospectforge.cli run-summary --run-id <the-run-id>
```

This prints the run's status, which stages completed, and — if it failed — the exact
`failed_stage` and error message. That error message is written by whichever stage actually
raised (see `app/trigger.py`'s docstring for the run-level vs. per-item failure distinction):

- **`load_icp`** - the ICP config id doesn't exist. Check `prospectforge/icp/configs/` for the
  actual id, or that `ICP_CONFIG_ID` in the request matches a real file.
- **`discovery` / `prefilter` / `fit_evaluation` / `dedup`** - these fail the *whole run* (no
  partial result to save at that point). The error message names the actual exception; a
  `RetryableError` that exhausted its attempts here almost always means a provider outage -
  check `check-provider-health` below.
- Any other stage (`enrichment`, `research`, `people_discovery`, `contact_enrichment`,
  `qualification`) - a `PARTIAL_SUCCESS` status here is normal, not a bug: these stages isolate
  failures per-item. Check the stage's own summary dict (also in `run-summary`'s output) for
  `*_failed` counts.

## "Some accounts/contacts seem stuck"

Every `_failed` status (`enrichment_failed`, `research_failed`,
`ContactStatus.enrichment_failed`) is retried automatically the *next time that stage runs* -
it's not a dead end (fixed at Step 19; see `docs/failure-scenario-coverage.md` for the bug this
closed). If an item still looks stuck after a fresh run:

```bash
python -m prospectforge.cli check-provider-health --provider apollo
python -m prospectforge.cli check-provider-health --provider anthropic
python -m prospectforge.cli check-provider-health --provider hubspot
```

If a provider is above its failure-rate threshold, this is likely the root cause - the
provider itself is degraded, not a bug in this codebase. Check the provider's own status page.

## "Is the provider actually down, or is our key wrong?"

Query `external_call_attempts` directly for the specific provider/operation:

```sql
SELECT status, error_message, requested_at
FROM external_call_attempts
WHERE provider = 'apollo' AND operation = 'account_enrichment'
ORDER BY requested_at DESC LIMIT 20;
```

`failed_non_retryable` with a 401/403 in `error_message` → bad or expired API key, not an
outage. `failed_retryable`/`failed_exhausted` with a 429/5xx → the provider is genuinely
struggling; the circuit breaker (Step 19) should already be limiting the damage.

## "The review queue is backing up"

```bash
python -m prospectforge.cli review-queue      # see what's pending, ranked, with rationale
python -m prospectforge.cli review-report      # approval/rejection rates
python -m prospectforge.cli reject-all-pending --reason "..."   # bulk triage
```

## "A prospect isn't showing up in HubSpot"

Only `ProspectRecord`s with `review_decision=APPROVED` are ever attempted (see
`crm/sync_service.py`). Check the review decision first (`review-report`), then re-run:

```bash
python -m prospectforge.cli sync-to-crm
```

This is safe to re-run - it only processes records with `synced_at IS NULL`, so an already-
synced record is never re-attempted or duplicated (idempotent search-then-create in the
HubSpot adapter itself, in any case - see ADR and `crm/adapters/hubspot.py`'s docstring).

## "A contact asked to be forgotten" (GDPR)

```bash
python -m prospectforge.cli erase-contact --contact-id <uuid>
```

Scrubs name/email/email_confidence/linkedin_url, redacts that contact's `ProviderRecord`
payloads and `ExternalCallAttempt` error messages, and permanently excludes them from any
future enrichment retry. See `docs/security-privacy-review.md` for exactly what is and isn't
touched, and why.

## "Sentry/logs show an error, what next?"

Every log line and Sentry event carries `run_id` (and `account_id`/`contact_id` where
applicable) as correlated tags - use that id with `run-summary` above to see the full picture,
not just the one error line.

## Local vs. deployed

Locally: Postgres via `docker compose up -d db`, app via `uvicorn app.main:app --reload`.
Deployed (Render): `docker-entrypoint.sh` runs `alembic upgrade head` on every start before the
server comes up - if a deploy fails, check the Render deploy log for the migration step
specifically, not just the server-start step. See `docs/deployment.md`.
