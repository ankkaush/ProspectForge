import uuid

from app.orm import AccountORM, EvidenceORM, RunORM
from infra.retry import NonRetryableError
from prospectforge.models import Account, ConfidenceLevel, Evidence, EvidenceSourceType
from prospectforge.models.enums import AccountStatus, FitTier, RunStatus
from prospectforge.research.interface import ResearchProvider, ResearchResult
from prospectforge.research.service import run_research


def _bare_run(db_session) -> RunORM:
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


def _fit_evaluated_account(db_session, fit_tier, **overrides) -> AccountORM:
    defaults = dict(
        id=uuid.uuid4(),
        domain=f"{uuid.uuid4().hex[:8]}.example.com",
        name="Example Co",
        status=AccountStatus.FIT_EVALUATED,
        fit_tier=fit_tier,
    )
    defaults.update(overrides)
    account = AccountORM(**defaults)
    db_session.add(account)
    db_session.flush()
    return account


class _PerDomainResearchProvider(ResearchProvider):
    def __init__(self, outcomes: dict):
        self._outcomes = outcomes  # domain -> ResearchResult | Exception

    def research_account(self, account: Account) -> ResearchResult:
        outcome = self._outcomes[account.domain]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _evidence_result(account_id=None, n=1) -> ResearchResult:
    return ResearchResult(
        evidence=[
            Evidence(
                account_id=account_id or uuid.uuid4(),
                claim=f"Signal {i}",
                source_type=EvidenceSourceType.AI_INFERRED,
                source_url="https://example.com",
                confidence=ConfidenceLevel.MEDIUM,
            )
            for i in range(n)
        ]
    )


# --- tier-based routing: research vs. skip-and-reject ----------------------

def test_tier_1_account_is_researched(db_session):
    run = _bare_run(db_session)
    account = _fit_evaluated_account(db_session, FitTier.TIER_1)
    provider = _PerDomainResearchProvider({account.domain: _evidence_result(account.id)})

    summary = run_research(run.id, db_session, provider=provider)

    assert summary["researched"] == 1
    assert summary["not_pursued"] == 0
    db_session.refresh(account)
    assert account.status == AccountStatus.RESEARCHED

    evidence = db_session.query(EvidenceORM).filter_by(account_id=account.id).all()
    assert len(evidence) == 1


def test_tier_2_account_is_researched(db_session):
    run = _bare_run(db_session)
    account = _fit_evaluated_account(db_session, FitTier.TIER_2)
    provider = _PerDomainResearchProvider({account.domain: _evidence_result(account.id)})

    run_research(run.id, db_session, provider=provider)

    db_session.refresh(account)
    assert account.status == AccountStatus.RESEARCHED


def test_insufficient_data_account_is_researched_not_skipped(db_session):
    """Extends the roadmap's literal 'Tier 1/2 only' scoping - see
    service.py's module docstring for why: Steps 8 and 10 both established
    that missing data never means automatic rejection, and this is the
    step where that principle matters most (it's what decides whether an
    account is pursued at all)."""

    run = _bare_run(db_session)
    account = _fit_evaluated_account(db_session, FitTier.INSUFFICIENT_DATA)
    provider = _PerDomainResearchProvider({account.domain: _evidence_result(account.id)})

    summary = run_research(run.id, db_session, provider=provider)

    assert summary["researched"] == 1
    assert summary["not_pursued"] == 0
    db_session.refresh(account)
    assert account.status == AccountStatus.RESEARCHED


def test_tier_3_account_is_rejected_without_a_research_call(db_session):
    run = _bare_run(db_session)
    account = _fit_evaluated_account(db_session, FitTier.TIER_3)
    provider = _PerDomainResearchProvider({})  # no outcome configured - must not be called

    summary = run_research(run.id, db_session, provider=provider)

    assert summary["not_pursued"] == 1
    assert summary["researched"] == 0
    db_session.refresh(account)
    assert account.status == AccountStatus.REJECTED


def test_already_rejected_tier_account_is_rejected_without_a_research_call(db_session):
    run = _bare_run(db_session)
    account = _fit_evaluated_account(db_session, FitTier.REJECTED)
    provider = _PerDomainResearchProvider({})

    run_research(run.id, db_session, provider=provider)

    db_session.refresh(account)
    assert account.status == AccountStatus.REJECTED


# --- per-item failure isolation --------------------------------------------

def test_one_account_failing_research_does_not_block_the_rest(db_session):
    run = _bare_run(db_session)
    account_a = _fit_evaluated_account(db_session, FitTier.TIER_1)
    account_b = _fit_evaluated_account(db_session, FitTier.TIER_1)
    provider = _PerDomainResearchProvider(
        {
            account_a.domain: NonRetryableError("401 unauthorized"),
            account_b.domain: _evidence_result(account_b.id),
        }
    )

    summary = run_research(run.id, db_session, provider=provider)

    assert summary["research_failed"] == 1
    assert summary["researched"] == 1

    db_session.refresh(account_a)
    db_session.refresh(account_b)
    assert account_a.status == AccountStatus.RESEARCH_FAILED
    assert account_b.status == AccountStatus.RESEARCHED


# --- summary and scoping ----------------------------------------------------

def test_only_processes_accounts_at_fit_evaluated_status(db_session):
    run = _bare_run(db_session)
    raw_account = _fit_evaluated_account(db_session, FitTier.TIER_1, status=AccountStatus.RAW)
    provider = _PerDomainResearchProvider({})

    summary = run_research(run.id, db_session, provider=provider)

    assert summary["evaluated"] == 0
    db_session.refresh(raw_account)
    assert raw_account.status == AccountStatus.RAW


def test_an_account_stuck_at_research_failed_gets_retried_and_can_succeed(db_session):
    """Step 19's idempotency-review fix: RESEARCH_FAILED is a legal retry
    state (RESEARCH_FAILED -> RESEARCHED in ACCOUNT_STATUS_TRANSITIONS),
    but this stage's query originally only looked at FIT_EVALUATED,
    silently orphaning every failed account forever."""

    run = _bare_run(db_session)
    account = _fit_evaluated_account(db_session, FitTier.TIER_1, status=AccountStatus.RESEARCH_FAILED)
    provider = _PerDomainResearchProvider({account.domain: _evidence_result(n=1)})

    summary = run_research(run.id, db_session, provider=provider)

    assert summary["evaluated"] == 1
    assert summary["researched"] == 1
    db_session.refresh(account)
    assert account.status == AccountStatus.RESEARCHED


def test_summary_tracks_dropped_claims(db_session):
    run = _bare_run(db_session)
    account = _fit_evaluated_account(db_session, FitTier.TIER_1)
    result_with_drops = ResearchResult(evidence=[], dropped_claim_count=2)
    provider = _PerDomainResearchProvider({account.domain: result_with_drops})

    summary = run_research(run.id, db_session, provider=provider)

    assert summary["claims_dropped"] == 2
    assert summary["evidence_collected"] == 0
    assert summary["researched"] == 1  # still a completed research attempt
