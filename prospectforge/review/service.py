"""The human review checkpoint (Step 17): gates CRM sync behind an
explicit human decision on each prioritized prospect.

This is deliberately NOT wired into app/trigger.py's start_run() - every
other stage in this pipeline is something a machine decides on its own;
review is the one step that can't be, by definition. It operates on
ProspectRecord rows a completed run already produced (see
prioritization/service.py), on the reviewer's own schedule, independent of
when any particular run happened.

Review decisions are recorded per ProspectRecord (per account+contact
pair), not per Account - an account with two qualified decision-makers
gets two independently reviewable records, matching how qualification and
prioritization already treat them. Account.status only moves
QUALIFIED -> REVIEWED as a coarse "a decision has been made for this
account" marker (using the state machine ACCOUNT_STATUS_TRANSITIONS
already defines for it) - it is NOT re-derived from the approve/reject
outcome. Step 18's actual sync-eligibility gate is expected to filter on
ProspectRecord.review_decision == APPROVED directly, the same field this
module writes, not on Account.status.

No AI, no scoring here - a human decision is exactly that, recorded
verbatim with a required reason on rejection (approval needs no
justification; the ranked queue itself is the justification for looking
at this record at all).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.mappers import orm_to_account
from app.orm import AccountORM, ContactORM, ProspectRecordORM, QualificationResultORM
from prospectforge.models.enums import AccountStatus, ReviewDecision


class ReviewError(Exception):
    """Raised for an invalid review action: an unknown prospect record, or
    an attempt to review one that already has a decision recorded."""


def _advance_account_to_reviewed(account_orm: AccountORM) -> None:
    """Idempotent: a QUALIFIED account moves to REVIEWED on its first
    review decision; an account with a second ProspectRecord reviewed
    later is already there, so this is a no-op the second time. Any other
    starting status would be a genuine bug upstream (a ProspectRecord
    shouldn't exist for an account that was never QUALIFIED), so this
    intentionally lets Account.transition_to's IllegalStatusTransition
    surface rather than swallowing it."""

    if account_orm.status == AccountStatus.REVIEWED:
        return
    account = orm_to_account(account_orm)
    account.transition_to(AccountStatus.REVIEWED)
    account_orm.status = account.status


def _get_pending_record(prospect_record_id: uuid.UUID, session: Session) -> ProspectRecordORM:
    record = session.get(ProspectRecordORM, prospect_record_id)
    if record is None:
        raise ReviewError(f"No ProspectRecord found with id={prospect_record_id}")
    if record.review_decision != ReviewDecision.PENDING:
        raise ReviewError(
            f"ProspectRecord {prospect_record_id} was already reviewed "
            f"(decision={record.review_decision.value}) - review decisions are not re-editable"
        )
    return record


def approve_prospect(prospect_record_id: uuid.UUID, session: Session) -> ProspectRecordORM:
    record = _get_pending_record(prospect_record_id, session)
    record.review_decision = ReviewDecision.APPROVED
    record.reviewed_at = datetime.now(timezone.utc)

    account_orm = session.get(AccountORM, record.account_id)
    _advance_account_to_reviewed(account_orm)

    session.flush()
    return record


def reject_prospect(prospect_record_id: uuid.UUID, session: Session, *, reason: str) -> ProspectRecordORM:
    if not reason or not reason.strip():
        raise ReviewError("A rejection requires a non-empty reason")

    record = _get_pending_record(prospect_record_id, session)
    record.review_decision = ReviewDecision.REJECTED
    record.review_reason = reason.strip()
    record.reviewed_at = datetime.now(timezone.utc)

    account_orm = session.get(AccountORM, record.account_id)
    _advance_account_to_reviewed(account_orm)

    session.flush()
    return record


def bulk_reject_pending(session: Session, *, reason: str) -> int:
    """The named failure scenario from the roadmap: an unbounded pending
    queue with no bulk-triage path. Rejects every still-PENDING record
    with the same reason; already-decided records are left untouched."""

    if not reason or not reason.strip():
        raise ReviewError("A rejection requires a non-empty reason")

    pending = session.query(ProspectRecordORM).filter_by(review_decision=ReviewDecision.PENDING).all()
    count = 0
    for record in pending:
        record.review_decision = ReviewDecision.REJECTED
        record.review_reason = reason.strip()
        record.reviewed_at = datetime.now(timezone.utc)
        account_orm = session.get(AccountORM, record.account_id)
        _advance_account_to_reviewed(account_orm)
        count += 1

    session.flush()
    return count


def list_pending_review(session: Session) -> List[Dict[str, Any]]:
    """The ranked review queue: every still-PENDING ProspectRecord, best
    priority first, with enough context (account, contact, rationale) for
    a human to decide without a separate lookup."""

    records = (
        session.query(ProspectRecordORM)
        .filter_by(review_decision=ReviewDecision.PENDING)
        .order_by(ProspectRecordORM.priority_rank.asc().nulls_last())
        .all()
    )

    queue = []
    for record in records:
        account = session.get(AccountORM, record.account_id)
        contact = session.get(ContactORM, record.contact_id)
        qualification = session.get(QualificationResultORM, record.qualification_result_id)
        queue.append(
            {
                "prospect_record_id": record.id,
                "priority_rank": record.priority_rank,
                "priority_score": record.priority_score,
                "account_name": account.name if account else None,
                "account_domain": account.domain if account else None,
                "contact_name": contact.name if contact else None,
                "contact_title": contact.title if contact else None,
                "qualification_confidence": qualification.confidence if qualification else None,
                "rationale_text": qualification.rationale_text if qualification else None,
            }
        )
    return queue


def review_report(session: Session) -> Dict[str, Any]:
    """Approval/rejection rates - the data the roadmap says should
    eventually justify (or not) loosening the review gate."""

    total = session.query(ProspectRecordORM).count()
    approved = (
        session.query(ProspectRecordORM).filter_by(review_decision=ReviewDecision.APPROVED).count()
    )
    rejected = (
        session.query(ProspectRecordORM).filter_by(review_decision=ReviewDecision.REJECTED).count()
    )
    pending = total - approved - rejected
    decided = approved + rejected

    return {
        "total": total,
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "approval_rate": (approved / decided) if decided else None,
        "rejection_rate": (rejected / decided) if decided else None,
    }
