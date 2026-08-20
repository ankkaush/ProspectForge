"""Step 20's security/privacy review finding: crm/sync_service.py is the
only stage besides contact_enrichment that submits a contact's real email
to an external API (HubSpot) - a failed call's error message could echo
that email back (a common REST API validation-error pattern), so it must
never be logged raw. Same pattern as
test_contact_enrichment_logging.py - captures real formatted JSON log
output rather than trusting the code "looks like" it avoids the leak.
"""

import io
import json
import logging
import uuid

from app.logging import JsonFormatter
from app.orm import AccountORM, ContactORM, FitResultORM, ProspectRecordORM, QualificationResultORM
from infra.retry import NonRetryableError
from prospectforge.crm.interface import CRMAdapter, CRMSyncInput, CRMSyncResult
from prospectforge.crm.sync_service import run_crm_sync
from prospectforge.models.enums import AccountStatus, FitPassType, FitTier, QualificationStatus, ReviewDecision

KNOWN_EMAIL = "definitely-a-real-address@example.com"


class _FailingAdapterThatEchoesTheEmail(CRMAdapter):
    """Simulates the realistic risk: a validation error whose message
    happens to include the exact data that was submitted."""

    def sync_prospect(self, input: CRMSyncInput) -> CRMSyncResult:
        raise NonRetryableError(f"HubSpot returned HTTP 400: invalid contact {input.contact_email}")


def test_a_known_email_never_appears_in_formatted_log_output_even_on_failure(db_session):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())

    crm_logger = logging.getLogger("prospectforge.crm")
    original_handlers = crm_logger.handlers[:]
    original_propagate = crm_logger.propagate
    crm_logger.handlers = [handler]
    crm_logger.propagate = False
    crm_logger.setLevel(logging.INFO)

    try:
        account = AccountORM(
            id=uuid.uuid4(), domain="example.com", name="Example Co", status=AccountStatus.REVIEWED
        )
        db_session.add(account)
        db_session.flush()
        db_session.add(
            FitResultORM(account_id=account.id, pass_type=FitPassType.FULL, tier=FitTier.TIER_1, reasons=[])
        )
        contact = ContactORM(
            id=uuid.uuid4(), account_id=account.id, name="Jane Doe", title="VP of Sales", email=KNOWN_EMAIL
        )
        db_session.add(contact)
        db_session.flush()
        qual = QualificationResultORM(
            account_id=account.id, contact_id=contact.id, status=QualificationStatus.QUALIFIED,
            reasons=[], confidence=0.8,
        )
        db_session.add(qual)
        db_session.flush()
        db_session.add(
            ProspectRecordORM(
                account_id=account.id, contact_id=contact.id, qualification_result_id=qual.id,
                priority_rank=1, review_decision=ReviewDecision.APPROVED,
            )
        )
        db_session.flush()

        run_crm_sync(db_session, adapter=_FailingAdapterThatEchoesTheEmail())
    finally:
        crm_logger.handlers = original_handlers
        crm_logger.propagate = original_propagate

    log_output = stream.getvalue()

    assert KNOWN_EMAIL not in log_output

    lines = [json.loads(line) for line in log_output.strip().splitlines()]
    assert any("CRM sync failed for contact_id=" in line["message"] for line in lines)
    assert any("NonRetryableError" in line["message"] for line in lines)
