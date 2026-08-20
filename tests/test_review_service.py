import uuid

import pytest

from app.orm import AccountORM, ContactORM, FitResultORM, ProspectRecordORM, QualificationResultORM
from prospectforge.models.enums import (
    AccountStatus,
    FitPassType,
    FitTier,
    QualificationStatus,
    ReviewDecision,
)
from prospectforge.review.service import (
    ReviewError,
    approve_prospect,
    bulk_reject_pending,
    list_pending_review,
    reject_prospect,
    review_report,
)


def _prospect_record(db_session, *, priority_rank=1, account_status=AccountStatus.QUALIFIED) -> ProspectRecordORM:
    account = AccountORM(
        id=uuid.uuid4(), domain=f"{uuid.uuid4().hex[:8]}.example.com", name="Example Co",
        status=account_status,
    )
    db_session.add(account)
    db_session.flush()

    db_session.add(
        FitResultORM(account_id=account.id, pass_type=FitPassType.FULL, tier=FitTier.TIER_1, reasons=[])
    )
    contact = ContactORM(id=uuid.uuid4(), account_id=account.id, name="Jane Doe", title="VP of Sales")
    db_session.add(contact)
    db_session.flush()

    qual = QualificationResultORM(
        account_id=account.id, contact_id=contact.id, status=QualificationStatus.QUALIFIED,
        reasons=["Tier 1 fit."], confidence=0.8, rationale_text="Strong fit, verified contact.",
    )
    db_session.add(qual)
    db_session.flush()

    record = ProspectRecordORM(
        account_id=account.id, contact_id=contact.id, qualification_result_id=qual.id,
        priority_score=0.7, priority_rank=priority_rank,
    )
    db_session.add(record)
    db_session.flush()
    return record


def test_approve_records_decision_and_advances_account_to_reviewed(db_session):
    record = _prospect_record(db_session)

    approved = approve_prospect(record.id, db_session)

    assert approved.review_decision == ReviewDecision.APPROVED
    assert approved.reviewed_at is not None

    account = db_session.get(AccountORM, record.account_id)
    assert account.status == AccountStatus.REVIEWED


def test_reject_requires_a_non_empty_reason(db_session):
    record = _prospect_record(db_session)

    with pytest.raises(ReviewError, match="non-empty reason"):
        reject_prospect(record.id, db_session, reason="")

    with pytest.raises(ReviewError, match="non-empty reason"):
        reject_prospect(record.id, db_session, reason="   ")


def test_reject_records_decision_reason_and_advances_account_to_reviewed(db_session):
    record = _prospect_record(db_session)

    rejected = reject_prospect(record.id, db_session, reason="Contact left the company.")

    assert rejected.review_decision == ReviewDecision.REJECTED
    assert rejected.review_reason == "Contact left the company."
    assert rejected.reviewed_at is not None

    account = db_session.get(AccountORM, record.account_id)
    assert account.status == AccountStatus.REVIEWED


def test_cannot_review_an_already_decided_record(db_session):
    record = _prospect_record(db_session)
    approve_prospect(record.id, db_session)

    with pytest.raises(ReviewError, match="already reviewed"):
        approve_prospect(record.id, db_session)

    with pytest.raises(ReviewError, match="already reviewed"):
        reject_prospect(record.id, db_session, reason="too late")


def test_reviewing_an_unknown_prospect_id_raises(db_session):
    with pytest.raises(ReviewError, match="No ProspectRecord found"):
        approve_prospect(uuid.uuid4(), db_session)


def test_second_review_for_the_same_account_does_not_break_the_already_reviewed_status(db_session):
    """Two ProspectRecords for the same account (two candidate contacts) -
    the first decision moves the account to REVIEWED; the second decision
    must not attempt an illegal REVIEWED -> REVIEWED transition."""

    record_1 = _prospect_record(db_session)
    account_id = record_1.account_id

    contact_2 = ContactORM(id=uuid.uuid4(), account_id=account_id, name="Carlos Mendez", title="Director")
    db_session.add(contact_2)
    db_session.flush()
    qual_2 = QualificationResultORM(
        account_id=account_id, contact_id=contact_2.id, status=QualificationStatus.QUALIFIED,
        reasons=[], confidence=0.6,
    )
    db_session.add(qual_2)
    db_session.flush()
    record_2 = ProspectRecordORM(
        account_id=account_id, contact_id=contact_2.id, qualification_result_id=qual_2.id,
        priority_score=0.5, priority_rank=2,
    )
    db_session.add(record_2)
    db_session.flush()

    approve_prospect(record_1.id, db_session)
    rejected = reject_prospect(record_2.id, db_session, reason="Lower priority contact, already covered.")

    assert rejected.review_decision == ReviewDecision.REJECTED
    account = db_session.get(AccountORM, account_id)
    assert account.status == AccountStatus.REVIEWED


def test_list_pending_review_excludes_decided_records_and_orders_by_rank(db_session):
    low_priority = _prospect_record(db_session, priority_rank=2)
    high_priority = _prospect_record(db_session, priority_rank=1)
    already_decided = _prospect_record(db_session, priority_rank=3)
    approve_prospect(already_decided.id, db_session)

    queue = list_pending_review(db_session)

    assert [item["prospect_record_id"] for item in queue] == [high_priority.id, low_priority.id]
    assert queue[0]["account_name"] == "Example Co"
    assert queue[0]["rationale_text"] == "Strong fit, verified contact."


def test_bulk_reject_pending_only_touches_pending_records(db_session):
    already_approved = _prospect_record(db_session, priority_rank=1)
    approve_prospect(already_approved.id, db_session)
    pending_a = _prospect_record(db_session, priority_rank=2)
    pending_b = _prospect_record(db_session, priority_rank=3)

    count = bulk_reject_pending(db_session, reason="Batch triage: end of quarter cleanup.")

    assert count == 2
    db_session.refresh(pending_a)
    db_session.refresh(pending_b)
    db_session.refresh(already_approved)
    assert pending_a.review_decision == ReviewDecision.REJECTED
    assert pending_b.review_decision == ReviewDecision.REJECTED
    assert pending_a.review_reason == "Batch triage: end of quarter cleanup."
    assert already_approved.review_decision == ReviewDecision.APPROVED  # untouched


def test_bulk_reject_pending_requires_a_reason(db_session):
    with pytest.raises(ReviewError, match="non-empty reason"):
        bulk_reject_pending(db_session, reason="")


def test_review_report_reflects_mixed_decisions(db_session):
    approved = _prospect_record(db_session, priority_rank=1)
    rejected = _prospect_record(db_session, priority_rank=2)
    _prospect_record(db_session, priority_rank=3)  # left pending
    approve_prospect(approved.id, db_session)
    reject_prospect(rejected.id, db_session, reason="Bad fit on reflection.")

    report = review_report(db_session)

    assert report["total"] == 3
    assert report["approved"] == 1
    assert report["rejected"] == 1
    assert report["pending"] == 1
    assert report["approval_rate"] == pytest.approx(0.5)
    assert report["rejection_rate"] == pytest.approx(0.5)


def test_review_report_with_no_records_reports_none_for_rates(db_session):
    report = review_report(db_session)

    assert report["total"] == 0
    assert report["approval_rate"] is None
    assert report["rejection_rate"] is None
