import uuid

from app.orm import AccountORM, ExternalCallAttemptORM, ProviderRecordORM, RunORM
from infra.retry import NonRetryableError, RetryableError
from prospectforge.enrichment.interface import EnrichmentProvider, EnrichmentResult
from prospectforge.enrichment.service import run_enrichment
from prospectforge.models import Account
from prospectforge.models.enums import AccountStatus, CallStatus, RunStatus


def _bare_run(db_session) -> RunORM:
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


def _advanced_account(db_session, **overrides) -> AccountORM:
    defaults = dict(
        id=uuid.uuid4(),
        domain=f"{uuid.uuid4().hex[:8]}.example.com",
        name="Example Co",
        status=AccountStatus.ADVANCED,
    )
    defaults.update(overrides)
    account = AccountORM(**defaults)
    db_session.add(account)
    db_session.flush()
    return account


class _PerDomainProvider(EnrichmentProvider):
    """Returns a scripted outcome per domain - lets a test control exactly
    which accounts succeed, come back with no data, or fail."""

    def __init__(self, outcomes: dict):
        self._outcomes = outcomes  # domain -> EnrichmentResult | Exception

    def enrich_account(self, account: Account) -> EnrichmentResult:
        outcome = self._outcomes[account.domain]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_enriches_an_advanced_account_and_transitions_to_enriched(db_session):
    run = _bare_run(db_session)
    account = _advanced_account(db_session)
    provider = _PerDomainProvider(
        {
            account.domain: EnrichmentResult(
                found=True, tech_stack=["Salesforce"], funding_stage="Series A", growth_signal="hiring"
            )
        }
    )

    summary = run_enrichment(run.id, db_session, provider=provider)

    assert summary["enriched"] == 1
    assert summary["enrichment_failed"] == 0

    db_session.refresh(account)
    assert account.status == AccountStatus.ENRICHED
    assert account.tech_stack == ["Salesforce"]
    assert account.funding_stage == "Series A"
    assert account.growth_signal == "hiring"


def test_no_data_found_still_transitions_to_enriched_not_failed(db_session):
    """A provider genuinely having no data is a successful, completed
    enrichment attempt - not a failure. The account still moves forward;
    its post-enrichment fields just stay unknown."""

    run = _bare_run(db_session)
    account = _advanced_account(db_session)
    provider = _PerDomainProvider({account.domain: EnrichmentResult(found=False)})

    summary = run_enrichment(run.id, db_session, provider=provider)

    assert summary["enriched"] == 0
    assert summary["no_data_found"] == 1
    assert summary["enrichment_failed"] == 0

    db_session.refresh(account)
    assert account.status == AccountStatus.ENRICHED
    assert account.tech_stack is None


def test_one_account_failing_does_not_block_the_rest_of_the_batch(db_session):
    """The per-item failure isolation this step introduces: account A's
    enrichment call exhausts retries and fails, but account B still gets
    processed in the same run."""

    run = _bare_run(db_session)
    account_a = _advanced_account(db_session)
    account_b = _advanced_account(db_session)
    provider = _PerDomainProvider(
        {
            account_a.domain: NonRetryableError("401 unauthorized"),
            account_b.domain: EnrichmentResult(found=True, tech_stack=["HubSpot"]),
        }
    )

    summary = run_enrichment(run.id, db_session, provider=provider)

    assert summary["evaluated"] == 2
    assert summary["enrichment_failed"] == 1
    assert summary["enriched"] == 1

    db_session.refresh(account_a)
    db_session.refresh(account_b)
    assert account_a.status == AccountStatus.ENRICHMENT_FAILED
    assert account_b.status == AccountStatus.ENRICHED
    assert account_b.tech_stack == ["HubSpot"]


def test_failed_account_gets_an_external_call_attempt_logged(db_session):
    run = _bare_run(db_session)
    account = _advanced_account(db_session)
    provider = _PerDomainProvider({account.domain: RetryableError("timeout")})

    run_enrichment(run.id, db_session, provider=provider)

    attempts = (
        db_session.query(ExternalCallAttemptORM)
        .filter_by(run_id=run.id, account_id=account.id)
        .all()
    )
    assert len(attempts) >= 1
    assert attempts[-1].status in (CallStatus.FAILED_RETRYABLE, CallStatus.FAILED_EXHAUSTED)


def test_raw_payload_persisted_as_provider_record(db_session):
    run = _bare_run(db_session)
    account = _advanced_account(db_session)
    provider = _PerDomainProvider(
        {account.domain: EnrichmentResult(found=True, tech_stack=["Slack"], raw_payload={"name": "Example Co"})}
    )

    run_enrichment(run.id, db_session, provider=provider)

    record = (
        db_session.query(ProviderRecordORM)
        .filter_by(account_id=account.id, operation="account_enrichment")
        .one()
    )
    assert record.payload["name"] == "Example Co"


def test_only_processes_accounts_at_advanced_status(db_session):
    run = _bare_run(db_session)
    raw_account = _advanced_account(db_session, status=AccountStatus.RAW)
    provider = _PerDomainProvider({})

    summary = run_enrichment(run.id, db_session, provider=provider)

    assert summary["evaluated"] == 0
    db_session.refresh(raw_account)
    assert raw_account.status == AccountStatus.RAW  # untouched


def test_an_account_stuck_at_enrichment_failed_gets_retried_and_can_succeed(db_session):
    """Step 19's idempotency-review fix: ENRICHMENT_FAILED is a legal
    retry state per ACCOUNT_STATUS_TRANSITIONS, but this stage's query
    originally only looked at ADVANCED, silently orphaning every failed
    account forever. This account starts already at ENRICHMENT_FAILED
    (simulating a prior run's exhausted retries) and must be picked up by
    a fresh run_enrichment() call, not skipped."""

    run = _bare_run(db_session)
    account = _advanced_account(db_session, status=AccountStatus.ENRICHMENT_FAILED)
    provider = _PerDomainProvider(
        {account.domain: EnrichmentResult(found=True, tech_stack=["Salesforce"], funding_stage="Series B")}
    )

    summary = run_enrichment(run.id, db_session, provider=provider)

    assert summary["evaluated"] == 1
    assert summary["enriched"] == 1
    db_session.refresh(account)
    assert account.status == AccountStatus.ENRICHED
    assert account.tech_stack == ["Salesforce"]
