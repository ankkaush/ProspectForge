# Security & Privacy Review (Step 20)

Performed 2026-08-20, once the pipeline was complete end-to-end (Steps 1-19). Structured pass
per the roadmap: secrets audit, PII-in-logs audit, GDPR posture review, dependency
vulnerability check, git history check.

## 1. Secrets audit

- No secrets are hardcoded anywhere in `prospectforge/`, `app/`, or `infra/` - every API key
  is read from `Settings` (`app/config.py`), which loads from environment variables / `.env`.
- `.env` is listed in `.gitignore` and was never committed (see §5 - there is no git history
  at all yet).
- **Finding, fixed**: `.env.example` was missing two settings added in later steps
  (`QUALIFICATION_RATIONALE_PROVIDER` from Step 15, `HUBSPOT_API_KEY` from Step 18) - it had
  silently drifted out of sync with `Settings`. Brought back in sync; confirmed every field in
  `.env.example` is a placeholder or a non-secret default (e.g. the documented local Postgres
  dev URL), never a real value.
- No API key is ever passed to a logger or `print()` anywhere in the codebase (grepped for
  `api_key`/`API_KEY` near every logging/print call site - no matches).

## 2. PII-in-logs audit

Grepped every `logger.info/warning/error/debug` call and every `print()` outside `cli.py`
(whose prints are intentional, human-facing CLI output, not application logs) across the
codebase.

- **Finding, fixed**: `crm/sync_service.py`'s failure-path log line
  (`logger.info("CRM sync failed for contact_id=%s: %s", ..., exc)`) interpolated the raw
  exception text. That exception's message can be `f"HubSpot returned HTTP {status}:
  {response.text}"` - and HubSpot's contact-create call submits the contact's real email, so a
  validation error that echoes the request body back (a common REST API pattern) could have
  leaked it into the log stream. `contact_enrichment/service.py` (Step 13) had already
  anticipated this exact risk for its own failure log line and logs only `contact_id`, never
  the exception text - `crm/sync_service.py` (Step 18) was the one place that didn't inherit
  that discipline, since it's the *other* stage that submits a real email to an external API.
  Fixed to log `type(exc).__name__` instead of the full message; full detail remains available
  in `ExternalCallAttempt.error_message` (a DB column, not a log line) for real debugging.
  Regression test: `tests/test_crm_sync_logging.py`, same "capture real formatted JSON output
  and grep for a known email" pattern as `tests/test_contact_enrichment_logging.py`.
- Every other log line touching a `Contact` was already correct: names are logged (accepted,
  see ADR-009 - materially less sensitive than an email, and useful for debugging), emails
  never are. Confirmed by the existing `test_contact_enrichment_logging.py` plus the new
  `test_crm_sync_logging.py`.
- No `logger.exception(...)` calls exist anywhere (which would dump a full traceback,
  including local variables' `repr()` in some configurations) - every exception is caught and
  logged with an explicit, controlled message instead.
- No middleware or request-body logging exists in `app/main.py` - only `run_id`/
  `account_id`/`contact_id` correlation via `app/logging.py`'s contextvars.

## 3. GDPR posture review

The user operates from Germany; this pipeline processes real, if currently fictional, business
contact data (name, email, title, LinkedIn URL). ADR-009 (Step 13) already stated the lawful
basis and explicitly deferred two things to this step - both addressed below.

**Lawful basis.** Processing business contact data (a work email, a title, at a company) for
B2B outbound is standard practice under GDPR's *legitimate interest* basis, not consent - the
same basis every CRM this project could sync to (HubSpot, Salesforce) relies on for its own
customers. This is an engineering-relevant summary, not a substitute for real legal advice if
this project ever processes real people's data in production.

**Data minimization.** Unchanged since Step 13: only fields a salesperson would actually use
are collected (`name`, `title`, `seniority`, `department`, `email`, `email_confidence`,
`linkedin_url`) - there's no column to accidentally fill with more than that later without a
deliberate schema change.

**Right to erasure (Article 17) - built this step.** Previously a documented gap with no
mechanism at all. `prospectforge/privacy/erasure.py`'s `erase_contact()` now:
- Scrubs the fields that identify or contact a specific person - `name` (replaced with the
  literal `"[erased]"`, since the column is `NOT NULL`), `email`, `email_confidence`,
  `linkedin_url`.
- Deliberately **keeps** `title`, `seniority`, `department` - these describe a role, not a
  person, and this project's entire audit trail (why an account was pursued, who reviewed it,
  whether it synced to the CRM) would otherwise become unexplainable. "We contacted someone at
  this company who asked to be forgotten" is a business fact worth retaining, distinct from
  that person's contact details.
- Sets `ContactStatus.ERASED`, a new terminal status **deliberately excluded** from
  `contact_enrichment/service.py`'s retry query. Without this, Step 19's own orphan-retry fix
  (querying `DISCOVERED`/`ENRICHMENT_FAILED`) would silently re-fetch and repopulate the
  erased email on the very next pipeline run - proven by
  `test_an_erased_contact_is_never_reprocessed` in `tests/test_contact_enrichment_service.py`.
  This is the kind of interaction between two separately-reasonable design decisions that only
  shows up when you go looking for it, which is the whole point of this review.
- Also redacts the same contact's `ProviderRecord.payload` rows (the raw enrichment response,
  which contains the email verbatim) and `ExternalCallAttempt.error_message` rows (which can
  echo request/response content back on a failed call, same risk as §2's log-line finding, but
  landing in a DB column instead of a log stream). Scrubbing only `contacts.email` and leaving
  a full copy sitting in either of those tables would have made the erasure incomplete.
- Idempotent - erasing an already-erased contact is a no-op, not an error.
- Exposed via `python -m prospectforge.cli erase-contact --contact-id <uuid>`.
- Live-verified against the real dev Postgres database (a throwaway contact, deleted after)
  and via 7 unit tests (`tests/test_privacy_erasure.py`) plus the contact_enrichment
  regression test above.

**Retention - documented gap, not fixed.** No automatic deletion or retention-window exists
for contact data that was never explicitly erased. Not built this step: this is a
learning/portfolio project currently operating on fictional seed data with no real
end-users, so a scheduled retention job is disproportionate build effort relative to the
erasure mechanism above (which demonstrates the actual legal *capability*, the part a real
deployment couldn't ship without). A real production deployment would need one - the concrete
recommendation is a scheduled job flagging contacts untouched (no status change, no CRM sync
activity) past a defined window for manual review, mirroring the review-queue pattern already
built in Step 17, not a hard auto-delete.

## 4. Dependency vulnerability check

Ran `pip-audit` against `requirements.txt`. Found 8 advisories across 4 packages: `click`
8.1.8, `starlette` 0.49.3, `pytest` 8.4.2, `python-dotenv` 1.2.1.

**Finding: no fix is currently installable.** Checked every advisory's listed "fix version"
against the real package index (`pip index versions <package>`) - none of them exist as
released packages yet (e.g. `starlette`'s advisories list fix versions `1.0.1`-`1.3.1`, but
`0.49.3` is the newest version that actually exists on the index; same pattern for `click`
8.3.3, `pytest` 9.0.3, `python-dotenv` 1.2.2). Every package already has the latest real
release installed. This means the vulnerability database's advisory data is ahead of what's
actually published - there is nothing to upgrade to right now, not a gap being overlooked.

**Practical exposure, checked per advisory rather than assumed low-risk:**
- `click` (command injection in `click.edit()`): never called anywhere in this codebase -
  `click` is a transitive dependency (pulled in by another package), not used directly.
- `starlette` (Host-header/path reconstruction issues, `StaticFiles` Windows SSRF, class-based
  `HTTPEndpoint` verb lookup): this app uses neither `StaticFiles` nor `HTTPEndpoint`
  (`app/main.py`'s routes are plain `@app.get`/`@app.post` function handlers) - not reachable
  through how this codebase actually uses the framework.
- `pytest` (predictable `/tmp/pytest-of-{user}` naming): dev/test-only, no production
  exposure - this process never runs `pytest` outside a developer's or CI's own machine.
  `python-dotenv` (`set_key`/`unset_key` following symlinks): this codebase only ever *reads*
  `.env` via `pydantic-settings`, never calls `set_key`/`unset_key` - not exploitable through
  this codebase's usage.

**Action item, not a blocker**: re-run `pip-audit` periodically and take the real upgrade once
fixed versions actually ship.

## 5. Git history check

**Not yet a git repository** (`git status` confirms no `.git` directory exists). This means
there is no history to leak a secret, so this specific check is trivially satisfied - but it
also means the "confirmed-clean git history" deliverable can't be more than that until the
repository actually exists. Recommendation, not auto-executed here (initializing a repo and
making a first commit is the user's call, not something to do unilaterally mid-review): once
`git init` happens, the first commit should include `.gitignore` (already correct - excludes
`.env`) and `.env.example`, and never `.env` itself; a `git log -p -- .env` / `git log --all
--full-history -- .env` check after any future accidental `.env` commit would be the way to
confirm it never leaked, but with a from-scratch history there is nothing to check yet.

## Summary of changes made this step

| File | Change |
|---|---|
| `.env.example` | Added missing `QUALIFICATION_RATIONALE_PROVIDER`, `HUBSPOT_API_KEY` |
| `prospectforge/crm/sync_service.py` | Failure log no longer includes raw exception text |
| `prospectforge/models/enums.py` | Added `ContactStatus.ERASED` |
| `app/orm.py` | Added `ContactORM.erased_at` (migration `6fca51250783`) |
| `prospectforge/models/contact.py`, `app/mappers.py` | Added `erased_at` to the pydantic model + mapper |
| `prospectforge/privacy/erasure.py` (new) | `erase_contact()` - GDPR Article 17 |
| `prospectforge/contact_enrichment/service.py` | Query already excluded `ERASED` by construction - verified, not changed |
| `prospectforge/cli.py` | New `erase-contact` command |
| `tests/test_crm_sync_logging.py`, `tests/test_privacy_erasure.py` (new); `tests/test_contact_enrichment_service.py` (+1 test) | Regression coverage for both findings |

## Exit criteria assessment

*"You would be comfortable making the repository public today."* Secrets: clean. Logs: clean,
with one real finding fixed. Dependencies: no known exploitable path given how this codebase
actually uses each flagged library, and no fixed version exists yet to upgrade to regardless.
GDPR: lawful basis documented, minimization already in place, erasure now built and verified
end-to-end, retention gap explicitly documented rather than silently missing. The one
open item is that there's no git repository yet at all - once one exists, this review's
findings (not the code state before them) is what should go into the first commit.
