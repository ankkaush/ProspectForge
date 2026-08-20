import uuid

from app.orm import (
    AccountORM,
    ContactORM,
    FitResultORM,
    ProspectRecordORM,
    QualificationResultORM,
    RunORM,
)
from prospectforge.models.enums import (
    AccountStatus,
    FitPassType,
    FitTier,
    QualificationStatus,
    RunStatus,
)
from prospectforge.prioritization.service import run_prioritization


def _bare_run(db_session) -> RunORM:
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


def _qualified_prospect(db_session, *, fit_tier=FitTier.TIER_1, title="VP of Sales", confidence=0.8) -> tuple:
    account = AccountORM(
        id=uuid.uuid4(), domain=f"{uuid.uuid4().hex[:8]}.example.com", name="Example Co",
        status=AccountStatus.QUALIFIED,
    )
    db_session.add(account)
    db_session.flush()

    db_session.add(
        FitResultORM(account_id=account.id, pass_type=FitPassType.FULL, tier=fit_tier, reasons=[])
    )
    contact = ContactORM(id=uuid.uuid4(), account_id=account.id, name="Jane Doe", title=title)
    db_session.add(contact)
    db_session.flush()

    qual = QualificationResultORM(
        account_id=account.id, contact_id=contact.id, status=QualificationStatus.QUALIFIED,
        reasons=[], confidence=confidence,
    )
    db_session.add(qual)
    db_session.flush()
    return account, contact, qual


def test_creates_a_prospect_record_for_a_qualified_pair(db_session):
    run = _bare_run(db_session)
    account, contact, qual = _qualified_prospect(db_session)

    summary = run_prioritization(run.id, db_session)

    assert summary["prospects_scored"] == 1
    record = db_session.query(ProspectRecordORM).filter_by(account_id=account.id, contact_id=contact.id).one()
    assert record.qualification_result_id == qual.id
    assert record.priority_score is not None
    assert record.priority_rank == 1


def test_higher_tier_ranks_above_lower_tier(db_session):
    run = _bare_run(db_session)
    strong, _, _ = _qualified_prospect(db_session, fit_tier=FitTier.TIER_1)
    weak, _, _ = _qualified_prospect(db_session, fit_tier=FitTier.INSUFFICIENT_DATA)

    run_prioritization(run.id, db_session)

    strong_record = db_session.query(ProspectRecordORM).filter_by(account_id=strong.id).one()
    weak_record = db_session.query(ProspectRecordORM).filter_by(account_id=weak.id).one()
    assert strong_record.priority_rank < weak_record.priority_rank  # rank 1 is highest priority


def test_ranks_are_globally_unique_and_contiguous(db_session):
    run = _bare_run(db_session)
    for _ in range(4):
        _qualified_prospect(db_session)

    run_prioritization(run.id, db_session)

    ranks = sorted(r.priority_rank for r in db_session.query(ProspectRecordORM).all())
    assert ranks == [1, 2, 3, 4]


def test_only_qualified_accounts_are_scored(db_session):
    run = _bare_run(db_session)
    not_qualified = AccountORM(
        id=uuid.uuid4(), domain=f"{uuid.uuid4().hex[:8]}.example.com", name="Not Qualified Co",
        status=AccountStatus.NOT_QUALIFIED,
    )
    db_session.add(not_qualified)
    db_session.flush()

    summary = run_prioritization(run.id, db_session)

    assert summary["prospects_scored"] == 0
    assert db_session.query(ProspectRecordORM).count() == 0


def test_rerunning_updates_the_existing_record_rather_than_duplicating(db_session):
    run = _bare_run(db_session)
    account, contact, _ = _qualified_prospect(db_session)

    run_prioritization(run.id, db_session)
    first_count = db_session.query(ProspectRecordORM).count()

    run_prioritization(run.id, db_session)
    second_count = db_session.query(ProspectRecordORM).count()

    assert first_count == second_count == 1


def test_deterministic_tie_break_by_domain_when_scores_and_confidence_tie(db_session):
    """Two prospects with identical scores and confidence must still get
    a stable, deterministic order - by account domain, never by
    insertion/iteration order accident."""

    run = _bare_run(db_session)
    # identical tier, title, and confidence -> identical scores
    account_b, _, _ = _qualified_prospect(db_session, title="VP of Sales", confidence=0.8)
    account_b.domain = "zzz-later.com"
    account_a, _, _ = _qualified_prospect(db_session, title="VP of Sales", confidence=0.8)
    account_a.domain = "aaa-earlier.com"
    db_session.flush()

    run_prioritization(run.id, db_session)

    record_a = db_session.query(ProspectRecordORM).filter_by(account_id=account_a.id).one()
    record_b = db_session.query(ProspectRecordORM).filter_by(account_id=account_b.id).one()
    assert record_a.priority_rank < record_b.priority_rank  # "aaa..." sorts before "zzz..."


def test_rerun_with_different_weights_changes_only_scores_not_upstream_data(db_session):
    run = _bare_run(db_session)
    account, contact, _ = _qualified_prospect(db_session, fit_tier=FitTier.TIER_2, title="Manager")

    run_prioritization(run.id, db_session, weights={"fit": 0.9, "evidence": 0.05, "contact_seniority": 0.05})
    fit_heavy_score = db_session.query(ProspectRecordORM).filter_by(account_id=account.id).one().priority_score

    run_prioritization(run.id, db_session, weights={"fit": 0.1, "evidence": 0.1, "contact_seniority": 0.8})
    seniority_heavy_score = db_session.query(ProspectRecordORM).filter_by(account_id=account.id).one().priority_score

    assert fit_heavy_score != seniority_heavy_score
    # upstream data (the fit result, the contact) is untouched by re-scoring
    db_session.refresh(account)
    assert account.status == AccountStatus.QUALIFIED


def test_uses_the_most_recent_qualification_result_per_contact(db_session):
    """An account re-qualified in a later run shouldn't leave a stale
    ProspectRecord pointed at an old QualificationResult."""

    run = _bare_run(db_session)
    account, contact, old_qual = _qualified_prospect(db_session, confidence=0.5)

    newer_qual = QualificationResultORM(
        account_id=account.id, contact_id=contact.id, status=QualificationStatus.QUALIFIED,
        reasons=[], confidence=0.9,
    )
    db_session.add(newer_qual)
    db_session.flush()

    run_prioritization(run.id, db_session)

    record = db_session.query(ProspectRecordORM).filter_by(account_id=account.id).one()
    assert record.qualification_result_id == newer_qual.id
