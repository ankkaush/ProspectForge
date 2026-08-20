"""Step 21's deliberate chaos: the four scenarios the roadmap names by
example - kill the DB mid-write, feed malformed provider responses, force
LLM JSON corruption, simulate duplicate ingestion - run against the real
start_run() pipeline, confirming each produces the designed failure
behavior from earlier steps (a clear FAILED/PARTIAL_SUCCESS run, isolated
per-item failures, no duplicate rows, no hang), not some new, unhandled
failure mode. Unit tests already prove each adapter/service handles its
own malformed input correctly in isolation; what's new here is proving
they still behave correctly when it happens live, inside a real run.
"""

from __future__ import annotations

from sqlalchemy.exc import OperationalError

from app.orm import AccountORM
from app.trigger import start_run
from infra.retry import NonRetryableError
from prospectforge.enrichment.interface import EnrichmentProvider, EnrichmentResult
from prospectforge.models import Account
from prospectforge.models.enums import AccountStatus, RunStatus
from prospectforge.research.interface import ResearchProvider, ResearchResult
from tests.fakes import FakeDiscoveryProvider


# --- 1. Kill the DB mid-write -------------------------------------------

def test_a_db_write_failure_mid_run_produces_a_clear_failed_run_not_a_hang(db_session, monkeypatch):
    """Simulates the DB connection dying partway through a stage (a
    session.flush() that suddenly raises, as a real dropped connection
    would). Must not hang, must not silently lose the failure - the run
    itself must end up FAILED with a named failing stage, exactly the
    same run-level failure path a raw ICPConfigError already takes (see
    app/trigger.py's broad except Exception handler)."""

    real_flush = db_session.flush
    call_count = {"n": 0}

    def flaky_flush(*args, **kwargs):
        call_count["n"] += 1
        # The run's own initial flush (assigning run.id) is call #1; call
        # #2 is discovery persisting its second account - sever the
        # connection right there, simulating the DB dying mid-write
        # partway through a stage, not cleanly between stages.
        if call_count["n"] == 2:
            raise OperationalError("simulated connection loss", {}, Exception("server closed the connection"))
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", flaky_flush)

    run = start_run("saas-fictional-v1", db_session)

    assert run.status == RunStatus.FAILED
    assert run.summary["failed_stage"] == "discovery"
    assert "simulated connection loss" in run.summary["error"]


# --- 2. Malformed provider responses, several stages at once -----------

class _MalformedThenFineEnrichmentProvider(EnrichmentProvider):
    """The first account gets a response so malformed the provider can't
    make sense of it at all (simulating a provider returning something
    completely unexpected, not just "no data") - the second gets a normal
    successful result. Both must be handled without crashing the run."""

    def __init__(self):
        self.calls = []

    def enrich_account(self, account: Account) -> EnrichmentResult:
        self.calls.append(account)
        if len(self.calls) == 1:
            raise NonRetryableError("provider returned an unrecognized response shape")
        return EnrichmentResult(found=True, tech_stack=["Salesforce"], funding_stage="Series B")


def test_malformed_provider_response_on_one_account_does_not_stop_the_others(db_session):
    run = start_run(
        "saas-fictional-v1", db_session, enrichment_provider=_MalformedThenFineEnrichmentProvider()
    )

    assert run.status == RunStatus.PARTIAL_SUCCESS
    assert run.summary["enrichment"]["enrichment_failed"] == 1
    assert run.summary["enrichment"]["enriched"] == 1
    # the pipeline still ran every later stage for the account that did enrich
    assert run.summary["stages_run"][-1] == "prioritization"

    accounts = db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    assert {a.status for a in accounts} == {AccountStatus.ENRICHMENT_FAILED, AccountStatus.QUALIFIED}


# --- 3. Forced LLM JSON corruption ---------------------------------------

class _GarbledJSONResearchProvider(ResearchProvider):
    """Every real research provider does its own JSON parsing internally
    and is contractually required to never let malformed JSON escape as
    anything other than a clean, empty-evidence ResearchResult or a
    Retryable/NonRetryableError (see research/extractor.py). This
    provider simulates the failure mode that would happen if the
    Anthropic API returned something the extractor genuinely can't
    recover from - a NonRetryableError, the actual contract."""

    def research_account(self, account: Account) -> ResearchResult:
        raise NonRetryableError("Claude returned unparseable JSON after retry")


def test_llm_json_corruption_does_not_crash_the_run_and_the_account_is_still_reachable_later(db_session):
    run = start_run(
        "saas-fictional-v1", db_session, research_provider=_GarbledJSONResearchProvider()
    )

    assert run.status == RunStatus.PARTIAL_SUCCESS
    assert run.summary["research"]["research_failed"] == 2
    assert run.summary["research"]["researched"] == 0
    # no accounts reached qualification this run - all stuck at RESEARCH_FAILED
    assert run.summary["qualification"]["accounts_evaluated"] == 0

    accounts = db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    assert {a.status for a in accounts} == {AccountStatus.RESEARCH_FAILED}

    # Step 19's orphan-retry fix: a follow-up run with a healthy provider
    # picks these back up rather than leaving them stranded.
    from prospectforge.research.service import run_research
    from tests.fakes import FakeResearchProvider

    retry_summary = run_research(run.id, db_session, provider=FakeResearchProvider())
    assert retry_summary["evaluated"] == 2
    assert retry_summary["researched"] == 2


# --- 4. Duplicate ingestion ------------------------------------------------

def test_duplicate_ingestion_across_two_full_runs_does_not_create_duplicate_accounts(db_session):
    """Not "duplicates within one discovery page" (already covered at the
    unit level in test_discovery_service.py) - this runs the WHOLE
    pipeline twice with a discovery provider that returns the identical
    accounts both times, simulating someone re-triggering a run over data
    that was already ingested."""

    fixed_orgs = FakeDiscoveryProvider()._default_organizations()

    class _FixedDiscoveryProvider(FakeDiscoveryProvider):
        def __init__(self):
            super().__init__(organizations=fixed_orgs)

    run_1 = start_run("saas-fictional-v1", db_session, discovery_provider=_FixedDiscoveryProvider())
    assert run_1.summary["discovery"]["persisted_new"] == 2

    run_2 = start_run("saas-fictional-v1", db_session, discovery_provider=_FixedDiscoveryProvider())
    assert run_2.summary["discovery"]["persisted_new"] == 0
    assert run_2.summary["discovery"]["skipped_duplicate"] == 2

    domains = [org.account.domain for org in fixed_orgs]
    all_accounts_for_these_domains = (
        db_session.query(AccountORM).filter(AccountORM.domain.in_(domains)).all()
    )
    assert len(all_accounts_for_these_domains) == 2  # not 4
