"""GDPR right-to-erasure (Article 17) for Contact records - Step 20's
concrete remediation for the gap ADR-009 explicitly deferred here.

What counts as "personal data" for this: the fields that identify or let
someone contact a specific natural person - name, email, email_confidence
(meaningless without the email it describes), linkedin_url. Deliberately
NOT erased: title, seniority, department, and the account/pipeline
relationship itself (FitResult, Evidence, QualificationResult,
ProspectRecord, review decision, CRM sync state). Those describe a role
and a business decision, not a person, and this project's whole audit
trail (why an account was pursued, who reviewed it, whether it synced)
would otherwise become unexplainable - "we contacted someone at this
company who then asked to be forgotten" is exactly the kind of business
fact a real company needs to retain, distinct from that person's contact
details themselves.

Sets status=ERASED, a terminal status excluded from every retry query in
this codebase (see ContactStatus.ERASED's docstring) - without this, the
contact could get silently re-enriched with a fresh email on the next
pipeline run, undoing the erasure.

Idempotent: erasing an already-erased contact is a no-op (returns it
unchanged) rather than re-stamping erased_at or raising - "please forget
this person" asked twice should not be an error.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.orm import ContactORM, ExternalCallAttemptORM, ProviderRecordORM
from prospectforge.models.enums import ContactStatus

REDACTED_NAME = "[erased]"
REDACTED_PAYLOAD = {"redacted": True, "reason": "contact erasure request (GDPR Article 17)"}
REDACTED_ERROR_MESSAGE = "[redacted on contact erasure]"


class ErasureError(Exception):
    """Raised when the requested contact doesn't exist."""


def erase_contact(contact_id: uuid.UUID, session: Session) -> ContactORM:
    contact = session.get(ContactORM, contact_id)
    if contact is None:
        raise ErasureError(f"No Contact found with id={contact_id}")

    if contact.status == ContactStatus.ERASED:
        return contact  # already erased - idempotent no-op

    contact.name = REDACTED_NAME
    contact.email = None
    contact.email_confidence = None
    contact.linkedin_url = None
    contact.status = ContactStatus.ERASED
    contact.erased_at = datetime.now(timezone.utc)

    # The raw provider response(s) for this contact (contact_enrichment,
    # people_discovery) are stored verbatim in ProviderRecord.payload for
    # audit - including the email, since the provider returned it as part
    # of the response body. Erasing contacts.email alone would leave a
    # complete copy of it sitting right here; redact wholesale rather than
    # trying to selectively strip provider-specific keys, which would be
    # fragile and could miss it.
    for record in session.query(ProviderRecordORM).filter_by(contact_id=contact_id).all():
        record.payload = dict(REDACTED_PAYLOAD)

    # Same reasoning, for a different audit table: a failed call's error
    # message can echo request/response content back verbatim (see
    # crm/sync_service.py's Step 20 fix for the log-line version of this
    # same risk) - ExternalCallAttempt.error_message is a DB column, not
    # a log line, but it's still a place this contact's email could be
    # sitting in plain text.
    for attempt in session.query(ExternalCallAttemptORM).filter_by(contact_id=contact_id).all():
        if attempt.error_message is not None:
            attempt.error_message = REDACTED_ERROR_MESSAGE

    session.flush()
    return contact
