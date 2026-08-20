import uuid
from typing import List

import pytest

from app.orm import AccountORM, ContactORM, ExternalCallAttemptORM, FitResultORM, ProspectRecordORM, QualificationResultORM
from infra.retry import NonRetryableError
from prospectforge.crm.interface import CRMAdapter, CRMSyncInput, CRMSyncResult
from prospectforge.crm.sync_service import run_crm_sync
from prospectforge.models.enums import (
    AccountStatus,
    FitPassType,
    FitTier,
    QualificationStatus,
    ReviewDecision,
)


def _approved_prospect(
    db_session, *, contact_email="jane.doe@example.com", account_status=AccountStatus.REVIEWED
) -> ProspectRecordORM:
    account = AccountORM(
        id=uuid.uuid4(), domain=f"{uuid.uuid4().hex[:8]}.example.com", name="Example Co",
        status=account_status,
    )
    db_session.add(account)
    db_session.flush()

    db_session.add(
        FitResultORM(account_id=account.id, pass_type=FitPassType.FULL, tier=FitTier.TIER_1, reasons=[])
    )
    contact = ContactORM(
        id=uuid.uuid4(), account_id=account.id, name="Jane Doe", title="VP of Sales", email=contact_email
    )
    db_session.add(contact)
    db_session.flush()

    qual = QualificationResultORM(
        account_id=account.id, contact_id=contact.id, status=QualificationStatus.QUALIFIED,
        reasons=["Tier 1 fit."], confidence=0.8, rationale_text="Strong fit.",
    )
    db_session.add(qual)
    db_session.flush()

    record = ProspectRecordORM(
        account_id=account.id, contact_id=contact.id, qualification_result_id=qual.id,
        priority_score=0.7, priority_rank=1, review_decision=ReviewDecision.APPROVED,
    )
    db_session.add(record)
    db_session.flush()
    return record


class _FakeCRMAdapter(CRMAdapter):
    def __init__(self, *, raises=None):
        self.calls: List[CRMSyncInput] = []
        self._raises = raises
        self._counter = 0

    def sync_prospect(self, input: CRMSyncInput) -> CRMSyncResult:
        self.calls.append(input)
        if self._raises:
            raise self._raises
        self._counter += 1
        return CRMSyncResult(
            crm_contact_id=f"contact-{self._counter}",
            company_matched_existing=False,
            contact_matched_existing=False,
        )


def test_syncs_an_approved_record_and_persists_crm_id(db_session):
    record = _approved_prospect(db_session)
    adapter = _FakeCRMAdapter()

    summary = run_crm_sync(db_session, adapter=adapter)

    assert summary == {"evaluated": 1, "synced": 1, "skipped_no_email": 0, "sync_failed": 0}
    db_session.refresh(record)
    assert record.crm_object_id == "contact-1"
    assert record.synced_at is not None

    account = db_session.get(AccountORM, record.account_id)
    assert account.status == AccountStatus.SYNCED


def test_only_approved_and_not_yet_synced_records_are_processed(db_session):
    approved = _approved_prospect(db_session)
    pending_record = _approved_prospect(db_session)
    pending_record.review_decision = ReviewDecision.PENDING
    db_session.flush()
    rejected_record = _approved_prospect(db_session)
    rejected_record.review_decision = ReviewDecision.REJECTED
    db_session.flush()

    adapter = _FakeCRMAdapter()
    summary = run_crm_sync(db_session, adapter=adapter)

    assert summary["evaluated"] == 1
    assert len(adapter.calls) == 1
    assert adapter.calls[0].account_domain == db_session.get(AccountORM, approved.account_id).domain


def test_already_synced_records_are_not_resynced(db_session):
    record = _approved_prospect(db_session)
    adapter = _FakeCRMAdapter()
    run_crm_sync(db_session, adapter=adapter)

    summary = run_crm_sync(db_session, adapter=adapter)

    assert summary["evaluated"] == 0
    assert len(adapter.calls) == 1  # not called a second time


def test_contact_with_no_email_is_skipped_not_failed(db_session):
    _approved_prospect(db_session, contact_email=None)
    adapter = _FakeCRMAdapter()

    summary = run_crm_sync(db_session, adapter=adapter)

    assert summary["skipped_no_email"] == 1
    assert summary["synced"] == 0
    assert adapter.calls == []  # never even attempted the CRM call


def test_adapter_failure_is_isolated_and_does_not_mark_synced(db_session):
    record = _approved_prospect(db_session)
    adapter = _FakeCRMAdapter(raises=NonRetryableError("simulated HubSpot failure"))

    summary = run_crm_sync(db_session, adapter=adapter)

    assert summary["sync_failed"] == 1
    assert summary["synced"] == 0
    db_session.refresh(record)
    assert record.synced_at is None
    assert record.crm_object_id is None

    account = db_session.get(AccountORM, record.account_id)
    assert account.status == AccountStatus.REVIEWED  # unchanged, not advanced to SYNCED


def test_call_is_recorded_as_an_external_call_attempt_without_a_run_id(db_session):
    """CRM sync isn't scoped to a Run - the audit trail must still work
    without one (see infra/retry.py's docstring on why run_id is
    optional as of this step)."""

    record = _approved_prospect(db_session)
    adapter = _FakeCRMAdapter()

    run_crm_sync(db_session, adapter=adapter)

    attempt = (
        db_session.query(ExternalCallAttemptORM)
        .filter_by(contact_id=record.contact_id, operation="crm_sync")
        .one()
    )
    assert attempt.run_id is None
    assert attempt.provider == "hubspot"


def test_second_approved_record_for_the_same_account_does_not_break_the_already_synced_status(db_session):
    """Two approved ProspectRecords for the same account - the first sync
    moves the account to SYNCED; the second must not attempt an illegal
    SYNCED -> SYNCED transition."""

    record_1 = _approved_prospect(db_session)
    account_id = record_1.account_id

    contact_2 = ContactORM(
        id=uuid.uuid4(), account_id=account_id, name="Carlos Mendez", title="Director",
        email="carlos@example.com",
    )
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
        priority_score=0.5, priority_rank=2, review_decision=ReviewDecision.APPROVED,
    )
    db_session.add(record_2)
    db_session.flush()

    adapter = _FakeCRMAdapter()
    summary = run_crm_sync(db_session, adapter=adapter)

    assert summary["synced"] == 2
    account = db_session.get(AccountORM, account_id)
    assert account.status == AccountStatus.SYNCED


def test_default_adapter_requires_hubspot_api_key(monkeypatch):
    from app.config import get_settings, reset_settings_cache
    from prospectforge.crm.sync_service import get_default_crm_adapter

    monkeypatch.setenv("HUBSPOT_API_KEY", "")
    reset_settings_cache()
    try:
        with pytest.raises(RuntimeError, match="HUBSPOT_API_KEY"):
            get_default_crm_adapter()
    finally:
        reset_settings_cache()
