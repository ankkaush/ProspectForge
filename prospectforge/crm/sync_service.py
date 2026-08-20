"""Orchestrates CRM sync: finds every ProspectRecord a human has approved
(Step 17) that hasn't synced yet, and pushes each one to the CRM via the
configured CRMAdapter.

Deliberately NOT part of app/trigger.py's start_run() - like review, sync
depends on a human decision made on the reviewer's own schedule, not on
any one pipeline run. It queries by ProspectRecord state
(review_decision=APPROVED, synced_at IS NULL), the same resumability
pattern every other stage uses, just applied to a different table and
without a Run to scope it to (see infra/retry.py's docstring on why
call_with_retry's run_id is optional as of this step).

Per-item failure isolation, same as enrichment/research/contact
enrichment: one prospect's CRM call failing doesn't stop the batch. GDPR
note (Germany, per this project's data-minimization stance since Step
13): never log a contact's raw email - contact_id and account domain are
the only identifiers this module logs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.mappers import orm_to_account
from app.orm import AccountORM, ContactORM, ProspectRecordORM, QualificationResultORM
from infra.retry import NonRetryableError, RetryableError, call_with_retry
from prospectforge.models.enums import AccountStatus, ReviewDecision

from .interface import CRMAdapter, CRMSyncInput

logger = logging.getLogger("prospectforge.crm")


def get_default_crm_adapter() -> CRMAdapter:
    from .adapters.hubspot import HubSpotAdapter

    api_key = get_settings().hubspot_api_key
    if not api_key:
        raise RuntimeError(
            "HUBSPOT_API_KEY is not set - required to run CRM sync against the real "
            "HubSpot adapter. Set it in .env, or pass an explicit adapter to "
            "run_crm_sync() for testing."
        )
    return HubSpotAdapter(api_key=api_key)


def run_crm_sync(session: Session, *, adapter: Optional[CRMAdapter] = None) -> Dict[str, Any]:
    active_adapter = adapter or get_default_crm_adapter()

    summary = {
        "evaluated": 0,
        "synced": 0,
        "skipped_no_email": 0,
        "sync_failed": 0,
    }

    pending = (
        session.query(ProspectRecordORM)
        .filter_by(review_decision=ReviewDecision.APPROVED, synced_at=None)
        .all()
    )

    for record in pending:
        summary["evaluated"] += 1
        contact = session.get(ContactORM, record.contact_id)

        if not contact or not contact.email:
            logger.info("skipping CRM sync for contact_id=%s: no email on file", record.contact_id)
            summary["skipped_no_email"] += 1
            continue

        account = session.get(AccountORM, record.account_id)
        qualification = session.get(QualificationResultORM, record.qualification_result_id)

        sync_input = CRMSyncInput(
            account_name=account.name,
            account_domain=account.domain,
            account_industry=account.industry,
            contact_name=contact.name,
            contact_email=contact.email,
            contact_title=contact.title,
            qualification_confidence=qualification.confidence if qualification else 0.0,
            rationale_text=qualification.rationale_text if qualification else None,
        )

        try:
            result = call_with_retry(
                lambda: active_adapter.sync_prospect(sync_input),
                session=session,
                account_id=record.account_id,
                contact_id=record.contact_id,
                provider="hubspot",
                operation="crm_sync",
            )
        except (RetryableError, NonRetryableError) as exc:
            # Never log the exception's raw text here (Step 20's security
            # review finding) - unlike every other adapter's errors, this
            # one comes from a call that submitted the contact's real
            # email to HubSpot, so a validation error echoing the
            # request body back (a common REST API pattern) could leak
            # it into the log stream. Full detail is already durably
            # captured in ExternalCallAttempt.error_message via
            # call_with_retry - that's the place to look, not the log.
            # Same discipline contact_enrichment/service.py already
            # applies to its own failure log line, for the same reason.
            logger.info(
                "CRM sync failed for contact_id=%s: %s", record.contact_id, type(exc).__name__
            )
            summary["sync_failed"] += 1
            continue

        record.crm_object_id = result.crm_contact_id
        record.synced_at = datetime.now(timezone.utc)
        _advance_account_to_synced(account)
        summary["synced"] += 1

        logger.info(
            "synced contact_id=%s account_domain=%s -> crm_contact_id=%s "
            "(company_matched=%s, contact_matched=%s)",
            record.contact_id,
            account.domain,
            result.crm_contact_id,
            result.company_matched_existing,
            result.contact_matched_existing,
        )
        session.flush()

    logger.info("CRM sync completed with summary=%s", summary)
    return summary


def _advance_account_to_synced(account_orm: AccountORM) -> None:
    """Idempotent, same pattern as review/service.py's
    _advance_account_to_reviewed: the first successful sync for this
    account moves it REVIEWED -> SYNCED; a second ProspectRecord for the
    same account syncing later is already there, so this is a no-op."""

    if account_orm.status == AccountStatus.SYNCED:
        return
    account = orm_to_account(account_orm)
    account.transition_to(AccountStatus.SYNCED)
    account_orm.status = account.status
