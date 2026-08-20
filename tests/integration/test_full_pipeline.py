"""Step 21's core deliverable: one test exercising the WHOLE pipeline,
ICP through CRM sync, against fully mocked providers - not per-stage in
isolation (every earlier step already has that), but proving the stages
actually compose correctly end to end, including the two human-gated
stages (review, CRM sync) that are deliberately outside start_run() and
so have never been exercised in the same test as the automated ten.

This is the "second major milestone" from Step 18's roadmap language,
finally proven in one place: a prospect goes from a raw discovered
account all the way to a synced CRM record, with every intermediate
status transition and artifact checked along the way.
"""

from __future__ import annotations

from app.orm import AccountORM, ProspectRecordORM
from app.trigger import start_run
from prospectforge.crm.interface import CRMAdapter, CRMSyncInput, CRMSyncResult
from prospectforge.crm.sync_service import run_crm_sync
from prospectforge.models.enums import AccountStatus, ReviewDecision
from prospectforge.review.service import approve_prospect, reject_prospect


class _FakeCRMAdapter(CRMAdapter):
    def __init__(self):
        self.calls = []
        self._n = 0

    def sync_prospect(self, input: CRMSyncInput) -> CRMSyncResult:
        self.calls.append(input)
        self._n += 1
        return CRMSyncResult(
            crm_contact_id=f"hubspot-contact-{self._n}",
            company_matched_existing=False,
            contact_matched_existing=False,
        )


def test_a_prospect_travels_from_raw_discovery_to_synced_crm_record(db_session):
    # Stages 1-10, fully automated, exactly as a real trigger (CLI/API) would run them.
    run = start_run("saas-fictional-v1", db_session)
    assert run.summary["stages_run"] == [
        "discovery", "prefilter", "enrichment", "fit_evaluation", "research",
        "people_discovery", "contact_enrichment", "dedup", "qualification", "prioritization",
    ]
    assert run.summary["prioritization"]["prospects_scored"] == 2

    prospect_records = db_session.query(ProspectRecordORM).order_by(ProspectRecordORM.priority_rank).all()
    assert len(prospect_records) == 2
    assert {r.review_decision for r in prospect_records} == {ReviewDecision.PENDING}

    accounts = db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    assert {a.status for a in accounts} == {AccountStatus.QUALIFIED}

    # Stage 11: human review - deliberately outside start_run(). One
    # approved, one rejected, exactly the mixed-outcome case that matters.
    approved_record, rejected_record = prospect_records
    approve_prospect(approved_record.id, db_session)
    reject_prospect(rejected_record.id, db_session, reason="Lower priority - covered by the other contact")

    db_session.refresh(approved_record)
    db_session.refresh(rejected_record)
    assert approved_record.review_decision == ReviewDecision.APPROVED
    assert rejected_record.review_decision == ReviewDecision.REJECTED

    approved_account = db_session.get(AccountORM, approved_record.account_id)
    rejected_account = db_session.get(AccountORM, rejected_record.account_id)
    assert approved_account.status == AccountStatus.REVIEWED
    assert rejected_account.status == AccountStatus.REVIEWED  # both reviewed; decision lives on the record

    # Stage 12: CRM sync - also outside start_run(), also human-paced.
    # Only the approved record should ever reach the CRM.
    crm_adapter = _FakeCRMAdapter()
    sync_summary = run_crm_sync(db_session, adapter=crm_adapter)

    assert sync_summary["evaluated"] == 1
    assert sync_summary["synced"] == 1
    assert len(crm_adapter.calls) == 1
    assert crm_adapter.calls[0].account_domain == approved_account.domain

    db_session.refresh(approved_record)
    db_session.refresh(rejected_record)
    assert approved_record.crm_object_id == "hubspot-contact-1"
    assert approved_record.synced_at is not None
    assert rejected_record.crm_object_id is None  # never attempted - not approved

    db_session.refresh(approved_account)
    assert approved_account.status == AccountStatus.SYNCED

    # The rejected side of the pipeline is untouched by CRM sync - proves
    # the review gate actually gates, not just that sync happens to work.
    db_session.refresh(rejected_account)
    assert rejected_account.status == AccountStatus.REVIEWED


def test_a_second_run_against_the_same_data_does_not_disturb_already_synced_prospects(db_session):
    """Full-pipeline idempotency: once a prospect has traveled all the way
    to SYNCED, a completely independent later run touching different
    accounts must not perturb it."""

    from tests.fakes import FakeDiscoveryProvider

    first_provider = FakeDiscoveryProvider()
    run_1 = start_run("saas-fictional-v1", db_session, discovery_provider=first_provider)
    prospect_records = db_session.query(ProspectRecordORM).all()
    for record in prospect_records:
        approve_prospect(record.id, db_session)
    run_crm_sync(db_session, adapter=_FakeCRMAdapter())

    synced_ids = {r.id for r in db_session.query(ProspectRecordORM).all()}
    synced_states = {
        r.id: (r.crm_object_id, r.synced_at) for r in db_session.query(ProspectRecordORM).all()
    }

    # A second, independent run with entirely different accounts.
    second_provider = FakeDiscoveryProvider()
    run_2 = start_run("saas-fictional-v1", db_session, discovery_provider=second_provider)

    assert run_2.id != run_1.id
    for record_id, (crm_id, synced_at) in synced_states.items():
        record = db_session.get(ProspectRecordORM, record_id)
        assert record.crm_object_id == crm_id
        assert record.synced_at == synced_at
