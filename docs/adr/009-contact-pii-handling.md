# ADR-009: Contact PII Handling (Step 13)

## Context
Step 13 is the first stage that writes real personal data (email addresses) rather than
company-level data. The original project brief flagged GDPR/data-minimization as something
to apply "when we hit contact enrichment specifically" - this is that point. The user is
based in Germany, so GDPR applies to this project's real-world use even though this is a
learning exercise with fictional seed data.

## What's actually stored
Only fields directly useful for outbound sales, matching the original brief's "would a
salesperson use this field?" test: name, title, seniority, department, email,
email_confidence, linkedin_url. Nothing beyond that (no personal phone, no home address, no
social profiles beyond LinkedIn) is collected or has a column to hold it - there's no field
to accidentally fill in later without a deliberate schema change.

## Lawful basis (engineering-relevant summary, not legal advice)
Processing business contact data (a work email, a job title, at a company) for B2B outreach
is commonly justified under GDPR's "legitimate interest" basis, not consent - this is
standard practice for B2B sales tooling (the same basis HubSpot/Salesforce/Apollo's own
customers rely on). This project doesn't implement a formal legitimate-interest assessment or
a suppression/opt-out mechanism - that's real work appropriate for Step 20's full
security/privacy review, once there's a complete system to review, not invented ad hoc here.

## Logging: the concrete engineering rule
No log line in `contact_enrichment/service.py` (or anywhere else touching a Contact) ever
includes an email value - verified directly in `test_contact_enrichment_logging.py`, which
greps real formatted JSON log output for a known email string and asserts its absence. Emails
exist only in two places: the `contacts.email` column, and `ProviderRecord.payload` (the raw
enrichment response, kept for audit like every other provider call in this project) - never
in a log stream.

Contact *names* remain in logs (already true since Step 12) - a name alone, attached to a
company, is materially less sensitive than an email address and is needed for any log line
to be useful for debugging ("which contact enriched successfully"). This project draws that
line deliberately, not by omission.

## Retention
Not addressed here. This project has no automatic deletion or retention-window logic for
contact data at this stage - flagged as a real gap, deferred to Step 20 (the full
security/privacy review), which is scoped for exactly this kind of cross-cutting concern once
the system is complete enough to review as a whole.

## Consequences
- `is_plausible_email` (validators.py) exists specifically so a malformed value never gets
  silently treated as real PII to act on - "verified" should mean something.
- Every future stage that touches a Contact (Step 15 qualification, CRM sync) inherits this
  same logging discipline by convention; this ADR is the reference point for why.

## Addendum (Step 20)
The formal lawful-basis note, the right-to-erasure mechanism, and the retention-policy gap
this ADR deferred are now addressed in `docs/security-privacy-review.md`. Erasure is built
(`prospectforge/privacy/erasure.py`, `ContactStatus.ERASED`); retention remains a documented,
deliberate gap, not an oversight - see that document for the reasoning. Step 20's own review
also found that `crm/sync_service.py` (Step 18) hadn't inherited this ADR's logging
discipline - the one other stage that submits a real email to an external API - and fixed it.
