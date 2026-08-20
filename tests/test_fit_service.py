import uuid

from app.orm import AccountORM, FitResultORM, RunORM
from prospectforge.fit.service import run_full_evaluation, run_prefilter
from prospectforge.models.enums import AccountStatus, FitPassType, FitTier, RunStatus


def _bare_run(db_session) -> RunORM:
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


def _raw_account(db_session, **overrides) -> AccountORM:
    defaults = dict(
        id=uuid.uuid4(),
        domain=f"{uuid.uuid4().hex[:8]}.example.com",
        name="Example Co",
        status=AccountStatus.RAW,
    )
    defaults.update(overrides)
    account = AccountORM(**defaults)
    db_session.add(account)
    db_session.flush()
    return account


def _enriched_account(db_session, **overrides) -> AccountORM:
    defaults = dict(
        id=uuid.uuid4(),
        domain=f"{uuid.uuid4().hex[:8]}.example.com",
        name="Example Co",
        status=AccountStatus.ENRICHED,
        industry="Computer Software",
        employee_count=120,
        geography="United States",
        tech_stack=["Salesforce"],
        funding_stage="Series B",
    )
    defaults.update(overrides)
    account = AccountORM(**defaults)
    db_session.add(account)
    db_session.flush()
    return account


def test_advances_a_matching_account(db_session):
    run = _bare_run(db_session)
    account = _raw_account(
        db_session, industry="Computer Software", employee_count=120, geography="United States"
    )

    summary = run_prefilter(run.id, "saas-fictional-v1", db_session)

    assert summary["advanced"] >= 1
    db_session.refresh(account)
    assert account.status == AccountStatus.ADVANCED

    fit_result = db_session.query(FitResultORM).filter_by(account_id=account.id).one()
    assert fit_result.pass_type == FitPassType.PREFILTER
    assert fit_result.tier == FitTier.TIER_2


def test_rejects_a_disqualified_account(db_session):
    run = _bare_run(db_session)
    account = _raw_account(
        db_session, industry="Gambling & Casinos", employee_count=120, geography="United States"
    )

    run_prefilter(run.id, "saas-fictional-v1", db_session)

    db_session.refresh(account)
    assert account.status == AccountStatus.REJECTED_EARLY
    fit_result = db_session.query(FitResultORM).filter_by(account_id=account.id).one()
    assert fit_result.tier == FitTier.REJECTED


def test_only_processes_accounts_at_raw_status(db_session):
    """An account already past RAW (e.g. from a previous run's prefilter)
    should be left alone - run_prefilter only picks up what's still
    waiting at this stage, per the resumability design."""

    run = _bare_run(db_session)
    already_advanced = _raw_account(
        db_session,
        industry="Computer Software",
        employee_count=120,
        geography="United States",
        status=AccountStatus.ADVANCED,
    )

    summary = run_prefilter(run.id, "saas-fictional-v1", db_session)

    assert summary["evaluated"] == 0
    db_session.refresh(already_advanced)
    assert already_advanced.status == AccountStatus.ADVANCED  # unchanged
    assert (
        db_session.query(FitResultORM).filter_by(account_id=already_advanced.id).count() == 0
    )


def test_picks_up_raw_accounts_regardless_of_which_run_discovered_them(db_session):
    """The query is by status, not by discovered_in_run_id - an account
    left at RAW by an earlier run gets picked up by a later run's
    prefilter pass, which is the resumability behavior this design is
    meant to provide."""

    earlier_run = _bare_run(db_session)
    leftover = _raw_account(
        db_session,
        industry="Computer Software",
        employee_count=120,
        geography="United States",
        discovered_in_run_id=earlier_run.id,
    )

    later_run = _bare_run(db_session)
    summary = run_prefilter(later_run.id, "saas-fictional-v1", db_session)

    assert summary["evaluated"] == 1
    db_session.refresh(leftover)
    assert leftover.status == AccountStatus.ADVANCED


def test_summary_counts_insufficient_data_separately(db_session):
    run = _bare_run(db_session)
    _raw_account(db_session, industry="Computer Software", employee_count=120)  # no geography

    summary = run_prefilter(run.id, "saas-fictional-v1", db_session)

    assert summary["insufficient_data"] == 1
    assert summary["advanced"] == 1  # insufficient data still advances


# --- run_full_evaluation (Step 10) -----------------------------------------

def test_full_evaluation_tiers_an_enriched_account_and_moves_it_to_fit_evaluated(db_session):
    run = _bare_run(db_session)
    account = _enriched_account(db_session)  # matches every Tier 1 criterion

    summary = run_full_evaluation(run.id, "saas-fictional-v1", db_session)

    assert summary["evaluated"] == 1
    assert summary["tier_1"] == 1

    db_session.refresh(account)
    assert account.status == AccountStatus.FIT_EVALUATED
    assert account.fit_tier == FitTier.TIER_1

    fit_result = (
        db_session.query(FitResultORM)
        .filter_by(account_id=account.id, pass_type=FitPassType.FULL)
        .one()
    )
    assert fit_result.tier == FitTier.TIER_1


def test_disqualified_account_status_is_fit_evaluated_with_rejected_tier(db_session):
    """The state machine has no direct ENRICHED -> REJECTED transition -
    status tracks stage progress, tier (checked separately here) tracks
    the verdict. This is deliberate, per evaluator.py's docstring."""

    run = _bare_run(db_session)
    account = _enriched_account(db_session, industry="Gambling & Casinos")

    run_full_evaluation(run.id, "saas-fictional-v1", db_session)

    db_session.refresh(account)
    assert account.status == AccountStatus.FIT_EVALUATED  # not REJECTED - see docstring above
    assert account.fit_tier == FitTier.REJECTED


def test_only_processes_accounts_at_enriched_status(db_session):
    run = _bare_run(db_session)
    raw_account = _enriched_account(db_session, status=AccountStatus.RAW)

    summary = run_full_evaluation(run.id, "saas-fictional-v1", db_session)

    assert summary["evaluated"] == 0
    db_session.refresh(raw_account)
    assert raw_account.status == AccountStatus.RAW  # untouched


def test_picks_up_enriched_accounts_regardless_of_which_run_enriched_them(db_session):
    earlier_run = _bare_run(db_session)
    leftover = _enriched_account(db_session)

    later_run = _bare_run(db_session)
    summary = run_full_evaluation(later_run.id, "saas-fictional-v1", db_session)

    assert summary["evaluated"] == 1
    db_session.refresh(leftover)
    assert leftover.status == AccountStatus.FIT_EVALUATED


def test_summary_breaks_down_by_every_tier(db_session):
    run = _bare_run(db_session)
    _enriched_account(db_session)  # tier 1
    _enriched_account(db_session, tech_stack=None, funding_stage=None)  # tier 2
    _enriched_account(db_session, industry="Retail")  # tier 3
    _enriched_account(db_session, industry="Gambling & Casinos")  # rejected
    _enriched_account(db_session, geography=None)  # insufficient data

    summary = run_full_evaluation(run.id, "saas-fictional-v1", db_session)

    assert summary["evaluated"] == 5
    assert summary["tier_1"] == 1
    assert summary["tier_2"] == 1
    assert summary["tier_3"] == 1
    assert summary["rejected"] == 1
    assert summary["insufficient_data"] == 1
