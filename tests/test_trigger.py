from app.orm import AccountORM, ContactORM, RunORM
from app.trigger import start_run
from prospectforge.icp.loader import ICPConfigError
from prospectforge.models.enums import AccountStatus, ContactStatus, FitTier, QualificationStatus, RunStatus
from prospectforge.qualification.rationale import templated_rationale

# Captured at collection time, before conftest's autouse
# _fake_rationale_provider_by_default fixture monkeypatches it for every
# test - lets one test below exercise the real default resolution.
from prospectforge.qualification.service import (
    get_default_rationale_provider as real_get_default_rationale_provider,
)


def test_start_run_creates_a_run_row(db_session):
    """No db_session.commit() here - not needed for this test's own
    assertions, and this file previously had one, which (combined with a
    structural gap fixed in conftest.py's _clean_database_after_each_test
    at Step 14) let a test's fake accounts silently leak into later
    tests once dedup started doing a global scan. See that fixture's
    docstring for the full story."""

    run = start_run("saas-fictional-v1", db_session)

    assert run.id is not None
    assert run.icp_config_id == "saas-fictional-v1"
    assert run.status == RunStatus.COMPLETED
    assert run.completed_at is not None

    fetched = db_session.get(RunORM, run.id)
    assert fetched is not None
    assert fetched.icp_config_id == "saas-fictional-v1"


def test_start_run_runs_discovery_and_persists_accounts(db_session):
    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["stages_run"] == [
        "discovery",
        "prefilter",
        "enrichment",
        "fit_evaluation",
        "research",
        "people_discovery",
        "contact_enrichment",
        "dedup",
        "qualification",
        "prioritization",
    ]
    assert run.summary["discovery"]["persisted_new"] == 2  # the fake provider's two accounts

    persisted = (
        db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    )
    assert len(persisted) == 2
    assert {a.name for a in persisted} == {"Northstar Metrics", "Fieldglow"}


def test_start_run_prefilters_the_accounts_it_just_discovered(db_session):
    """The fake provider's two accounts both match the real seed ICP's
    tier 2 pre-enrichment criteria (software industry, 50-500 employees,
    a supported geography), so both should clear the prefilter, go on to
    be enriched, evaluated as Tier 1, and finally researched - all within
    this one start_run() call."""

    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["prefilter"]["evaluated"] == 2
    assert run.summary["prefilter"]["advanced"] == 2
    assert run.summary["prefilter"]["rejected_early"] == 0

    persisted = (
        db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    )
    assert {a.status for a in persisted} == {AccountStatus.QUALIFIED}


def test_start_run_enriches_the_accounts_that_advanced(db_session):
    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["enrichment"]["evaluated"] == 2
    assert run.summary["enrichment"]["enriched"] == 2
    assert run.summary["enrichment"]["enrichment_failed"] == 0
    assert run.status == RunStatus.COMPLETED  # no per-item failures this run

    persisted = (
        db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    )
    for account in persisted:
        assert account.tech_stack == ["Salesforce"]
        assert account.funding_stage == "Series B"
        assert account.growth_signal == "hiring"


def test_start_run_fully_evaluates_fit_after_enrichment(db_session):
    """The fake enrichment provider's tech_stack=['Salesforce'] and
    funding_stage='Series B' both satisfy the real seed ICP's Tier 1
    criteria, on top of the firmographics both accounts already had - so
    both should land as Tier 1 once the full evaluator runs."""

    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["fit_evaluation"]["evaluated"] == 2
    assert run.summary["fit_evaluation"]["tier_1"] == 2

    persisted = (
        db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    )
    for account in persisted:
        assert account.fit_tier == FitTier.TIER_1


def test_start_run_researches_tier_1_accounts(db_session):
    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["research"]["evaluated"] == 2
    assert run.summary["research"]["researched"] == 2
    assert run.summary["research"]["not_pursued"] == 0
    assert run.summary["research"]["evidence_collected"] == 2  # one per account, fake provider

    persisted = (
        db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    )
    for account in persisted:
        assert account.status == AccountStatus.QUALIFIED


def test_start_run_discovers_decision_makers_for_researched_accounts(db_session):
    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["people_discovery"]["accounts_evaluated"] == 2
    assert run.summary["people_discovery"]["accounts_with_contacts"] == 2
    assert run.summary["people_discovery"]["contacts_found"] == 2  # one per account, fake provider

    persisted_accounts = (
        db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    )
    for account in persisted_accounts:
        contacts = db_session.query(ContactORM).filter_by(account_id=account.id).all()
        assert len(contacts) == 1
        assert contacts[0].name == "Jane Doe"
        assert contacts[0].title == "VP of Revenue Operations"
        # people discovery doesn't change Account.status - see
        # people_discovery/service.py's module docstring. By the time
        # start_run() returns, qualification has already moved it on.
        assert account.status == AccountStatus.QUALIFIED


def test_start_run_enriches_the_contacts_it_just_found(db_session):
    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["contact_enrichment"]["evaluated"] == 2
    assert run.summary["contact_enrichment"]["enriched"] == 2
    assert run.summary["contact_enrichment"]["enrichment_failed"] == 0

    persisted_accounts = (
        db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    )
    for account in persisted_accounts:
        contacts = db_session.query(ContactORM).filter_by(account_id=account.id).all()
        assert len(contacts) == 1
        assert contacts[0].status == ContactStatus.ENRICHED
        assert contacts[0].email == "jane.doe@example.com"
        assert contacts[0].email_confidence == "verified"


def test_start_run_finds_no_duplicates_among_genuinely_distinct_accounts(db_session):
    """Northstar Metrics and Fieldglow are genuinely different companies -
    the dedup stage should report zero merges, not a false positive."""

    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["dedup"]["accounts_merged"] == 0
    assert run.summary["dedup"]["contacts_merged"] == 0
    assert run.summary["dedup"]["merges"] == []

    persisted = db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    assert len(persisted) == 2  # neither account was merged away


def test_start_run_qualifies_accounts_with_a_contact_and_good_fit(db_session):
    """Both fake accounts are Tier 1 with one contact each - both should
    qualify, each with the fake provider's rationale text attached."""

    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["qualification"]["accounts_evaluated"] == 2
    assert run.summary["qualification"]["accounts_qualified"] == 2
    assert run.summary["qualification"]["accounts_not_qualified"] == 0
    assert run.summary["qualification"]["qualification_results"] == 2
    assert run.summary["qualification"]["rationale_generated"] == 2
    assert run.summary["qualification"]["rationale_fallback"] == 0

    from app.orm import QualificationResultORM

    results = db_session.query(QualificationResultORM).all()
    assert len(results) == 2
    for result in results:
        assert result.status == QualificationStatus.QUALIFIED
        assert result.rationale_text == "Fake rationale: strong fit with a verified contact."
        assert result.confidence > 0


def test_start_run_qualifies_with_deterministic_rationale_and_no_anthropic_call(
    db_session, monkeypatch
):
    """The real, unpatched default: QUALIFICATION_RATIONALE_PROVIDER
    defaults to "deterministic", so the standard pipeline must complete
    Step 15 without making any Anthropic call at all. Bypasses this
    file's autouse fake-rationale-provider fixture (which simulates an
    AI-configured deployment for the test above) to prove it."""

    import prospectforge.qualification.service as qualification_service

    monkeypatch.setattr(
        qualification_service, "get_default_rationale_provider", real_get_default_rationale_provider
    )

    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["qualification"]["accounts_qualified"] == 2
    assert run.summary["qualification"]["rationale_deterministic"] == 2
    assert run.summary["qualification"]["rationale_generated"] == 0
    assert run.summary["qualification"]["rationale_fallback"] == 0

    from app.orm import QualificationResultORM

    results = db_session.query(QualificationResultORM).all()
    assert len(results) == 2
    for result in results:
        assert result.status == QualificationStatus.QUALIFIED
        assert result.rationale_text == templated_rationale(result.reasons)


def test_start_run_prioritizes_qualified_prospects(db_session):
    run = start_run("saas-fictional-v1", db_session)

    assert run.summary["prioritization"]["prospects_scored"] == 2

    from app.orm import ProspectRecordORM

    records = db_session.query(ProspectRecordORM).all()
    assert len(records) == 2
    ranks = sorted(r.priority_rank for r in records)
    assert ranks == [1, 2]  # every prospect got a distinct, assigned rank
    for record in records:
        assert record.priority_score is not None
        assert 0.0 <= record.priority_score <= 1.0


def test_provider_outage_mid_run_produces_a_clear_recoverable_state_not_a_hang(db_session):
    """Step 19's exit criteria, exercised end-to-end: a provider that's
    entirely unreachable (simulating the network to it being killed) must
    produce a clear PARTIAL_SUCCESS with the affected accounts parked at
    a named, retryable status - not a hang, a crash, or silently lost
    accounts. Uses NonRetryableError (fails immediately, no real sleep)
    rather than RetryableError purely so this test runs fast - the
    outcome being tested (clear state, not a hang) is the same either
    way; exact backoff timing is covered separately in test_retry.py."""

    from infra.retry import NonRetryableError as _NonRetryableError
    from prospectforge.enrichment.interface import EnrichmentProvider

    class _AlwaysDownEnrichmentProvider(EnrichmentProvider):
        def enrich_account(self, account):
            raise _NonRetryableError("simulated: provider unreachable")

    run = start_run(
        "saas-fictional-v1", db_session, enrichment_provider=_AlwaysDownEnrichmentProvider()
    )

    assert run.status == RunStatus.PARTIAL_SUCCESS  # not FAILED, not a hang
    assert run.summary["enrichment"]["enrichment_failed"] == 2
    assert run.summary["stages_run"][-1] == "prioritization"  # pipeline still ran to completion

    accounts = db_session.query(AccountORM).filter_by(discovered_in_run_id=run.id).all()
    assert {a.status for a in accounts} == {AccountStatus.ENRICHMENT_FAILED}

    # Step 19's idempotency fix: these aren't stuck - a follow-up call
    # with a healthy provider picks them right back up.
    from prospectforge.enrichment.service import run_enrichment
    from tests.fakes import FakeEnrichmentProvider

    retry_summary = run_enrichment(run.id, db_session, provider=FakeEnrichmentProvider())
    assert retry_summary["evaluated"] == 2
    assert retry_summary["enriched"] == 2


def test_start_run_fails_the_whole_run_for_an_unknown_icp(db_session):
    run = start_run("no-such-icp-config", db_session)

    assert run.status == RunStatus.FAILED
    assert run.summary["failed_stage"] == "load_icp"
    assert run.summary["stages_run"] == []


def test_two_runs_get_distinct_ids(db_session):
    run_a = start_run("saas-fictional-v1", db_session)
    run_b = start_run("saas-fictional-v1", db_session)
    assert run_a.id != run_b.id
