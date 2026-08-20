"""Verifies this step's explicit exit criteria: grep the logs for a real
contact's email - it should not appear in plaintext. Captures the actual
formatted JSON log output (the same JsonFormatter every part of this
project logs through - see app/logging.py) while running real contact
enrichment, then greps it directly, rather than trusting that the code
"looks like" it avoids logging the email.
"""

import io
import json
import logging
import uuid

from app.orm import AccountORM, ContactORM, RunORM
from prospectforge.contact_enrichment.interface import ContactEnrichmentProvider, ContactEnrichmentResult
from prospectforge.contact_enrichment.service import run_contact_enrichment
from app.logging import JsonFormatter
from prospectforge.models import Account, Contact
from prospectforge.models.enums import RunStatus

KNOWN_EMAIL = "definitely-a-real-address@example.com"


class _FixedEmailProvider(ContactEnrichmentProvider):
    def enrich_contact(self, contact: Contact, account: Account) -> ContactEnrichmentResult:
        return ContactEnrichmentResult(
            found=True,
            email=KNOWN_EMAIL,
            email_confidence="verified",
            raw_payload={"email": KNOWN_EMAIL},  # fine to appear in the DB payload, not in logs
        )


def test_a_known_email_never_appears_in_formatted_log_output(db_session):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())

    contact_enrichment_logger = logging.getLogger("prospectforge.contact_enrichment")
    original_handlers = contact_enrichment_logger.handlers[:]
    original_propagate = contact_enrichment_logger.propagate
    contact_enrichment_logger.handlers = [handler]
    contact_enrichment_logger.propagate = False
    contact_enrichment_logger.setLevel(logging.INFO)

    try:
        run = RunORM(id=uuid.uuid4(), icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
        db_session.add(run)
        account = AccountORM(id=uuid.uuid4(), domain="example.com", name="Example Co")
        db_session.add(account)
        db_session.flush()
        contact = ContactORM(id=uuid.uuid4(), account_id=account.id, name="Jane Doe")
        db_session.add(contact)
        db_session.flush()

        run_contact_enrichment(run.id, db_session, provider=_FixedEmailProvider())
    finally:
        contact_enrichment_logger.handlers = original_handlers
        contact_enrichment_logger.propagate = original_propagate

    log_output = stream.getvalue()

    assert KNOWN_EMAIL not in log_output
    assert "Jane Doe" in log_output  # names are fine - see ADR-009

    # Sanity check the log lines are still real, parseable JSON with
    # useful content - this isn't passing by accident because nothing
    # got logged at all.
    lines = [json.loads(line) for line in log_output.strip().splitlines()]
    assert any("enriched contact_id=" in line["message"] for line in lines)
