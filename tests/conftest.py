"""Shared test fixtures.

By default, tests run against a fresh, file-based SQLite database per test
session (not the dev database, and not production Postgres) - see
app/orm.py's docstring for why the schema is written to be portable across
both. This keeps the foundation test suite fast and dependency-free; full
Postgres-specific behavior is exercised at Step 21, against real
infrastructure.

Setting DATABASE_URL in the environment before running pytest overrides
this default - used to spot-check the suite against real Postgres (e.g.
after a schema-relevant change) without waiting for Step 21.
"""

import os
import uuid

import pytest

os.environ["PROSPECTFORGE_API_KEY"] = "test-api-key"
os.environ.setdefault("DATABASE_URL", f"sqlite:///./test_{uuid.uuid4().hex}.db")

from app import db as db_module  # noqa: E402
from app.config import reset_settings_cache  # noqa: E402
from app.orm import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _configure_test_environment():
    reset_settings_cache()
    db_module.reset_db_cache()
    Base.metadata.create_all(db_module.get_engine())
    yield
    db_path = db_module.get_settings().database_url.replace("sqlite:///./", "")
    db_module.get_engine().dispose()
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture()
def db_session():
    SessionLocal = db_module.get_sessionmaker()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _reset_observability():
    """infra/observability.py's init state and Sentry's global client are
    both process-global - without a reset, one test's sentry_sdk.init()
    (or lack of one) would bleed into every later test in the suite."""

    import sentry_sdk

    from infra import observability

    observability.reset_for_testing()
    yield
    observability.reset_for_testing()
    sentry_sdk.get_global_scope().set_client(None)  # NonRecordingClient - genuinely inactive


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """The default circuit breaker (infra/circuit_breaker.py) is
    process-global, keyed by provider name - without a reset, one test's
    simulated failures for "apollo"/"anthropic"/"hubspot" would bleed into
    a later, unrelated test using the same provider name."""

    from infra.circuit_breaker import default_circuit_breaker

    default_circuit_breaker.reset()
    yield
    default_circuit_breaker.reset()


@pytest.fixture(autouse=True)
def _clean_database_after_each_test():
    """Truncates every mutable table after each test, regardless of
    whether that test committed or relied on db_session's rollback.

    Added at Step 14: db_session's rollback only isolates tests that never
    commit. app/main.py's POST /runs endpoint legitimately commits (a real
    API request should durably persist its result) - so test_api.py's
    tests were quietly leaving fake "Northstar Metrics"/"Fieldglow"
    accounts permanently in the shared on-disk SQLite file. That was
    invisible before this step, because nothing compared data *across*
    tests - Step 14's dedup pass does exactly that (a global scan by name
    similarity), so leftover committed data from an earlier test file was
    silently merged into a later test's fresh accounts. Caught by running
    the full suite, not by inspection - see test_trigger.py's note on the
    same incident.

    Deleted in child-to-parent FK order so this also works correctly
    against real Postgres (which enforces foreign keys; SQLite in this
    project's test setup does not - see app/orm.py's docstring)."""

    yield
    SessionLocal = db_module.get_sessionmaker()
    session = SessionLocal()
    try:
        from app.orm import (
            AccountORM,
            ContactORM,
            EvidenceORM,
            ExternalCallAttemptORM,
            FitResultORM,
            ProspectRecordORM,
            ProviderRecordORM,
            QualificationResultORM,
            RunORM,
        )

        for model in (
            ExternalCallAttemptORM,
            ProviderRecordORM,
            EvidenceORM,
            FitResultORM,
            ProspectRecordORM,
            QualificationResultORM,
            ContactORM,
            AccountORM,
            RunORM,
        ):
            session.query(model).delete()
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _fake_discovery_provider_by_default(monkeypatch):
    """Every test gets a fresh FakeDiscoveryProvider wired in as the
    default, so start_run() never tries to reach real Apollo (no network
    access and no API key in this test environment). Tests that need the
    real Apollo-mapping/retry logic exercise ApolloDiscoveryProvider
    directly instead (see test_discovery_apollo_provider.py) - this
    fixture doesn't affect those, since they never call
    get_default_discovery_provider() at all."""

    from tests.fakes import FakeDiscoveryProvider

    fake = FakeDiscoveryProvider()
    monkeypatch.setattr(
        "prospectforge.discovery.service.get_default_discovery_provider", lambda: fake
    )
    return fake


@pytest.fixture(autouse=True)
def _fake_enrichment_provider_by_default(monkeypatch):
    """Same reasoning as the discovery fixture above, for enrichment.
    Necessary even though Apollo's enrichment endpoint is genuinely
    accessible on the project's free plan (unlike search) - a real
    APOLLO_API_KEY is present in .env, so without this patch, tests would
    make real network calls to Apollo on every run. Dedicated
    Apollo-enrichment tests exercise ApolloEnrichmentProvider directly with
    a mocked HTTP client instead (test_enrichment_apollo_provider.py)."""

    from tests.fakes import FakeEnrichmentProvider

    fake = FakeEnrichmentProvider()
    monkeypatch.setattr(
        "prospectforge.enrichment.service.get_default_enrichment_provider", lambda: fake
    )
    return fake


@pytest.fixture(autouse=True)
def _fake_research_provider_by_default(monkeypatch):
    """Same reasoning as the discovery/enrichment fixtures above, for
    research. A real ANTHROPIC_API_KEY is present in .env (Step 11's live
    verification used it), so without this patch every test would make a
    real, billed web-search call to Claude. Dedicated research logic
    (JSON parsing/retry, the source-verification cross-check) is tested
    directly against extractor.py and AnthropicWebSearchResearchProvider
    instead (test_research_extractor.py, test_research_anthropic_provider.py)."""

    from tests.fakes import FakeResearchProvider

    fake = FakeResearchProvider()
    monkeypatch.setattr(
        "prospectforge.research.service.get_default_research_provider", lambda: fake
    )
    return fake


@pytest.fixture(autouse=True)
def _fake_person_discovery_provider_by_default(monkeypatch):
    """Same reasoning as the fixtures above, for decision-maker discovery.
    Necessary even though the CSV provider is the real active default (no
    network call at all) - this keeps trigger/API-level tests independent
    of the seed contacts.csv's exact contents. Dedicated
    CSV/Apollo-provider logic is tested directly instead
    (test_people_discovery_csv_provider.py, test_people_discovery_apollo_provider.py)."""

    from tests.fakes import FakePersonDiscoveryProvider

    fake = FakePersonDiscoveryProvider()
    monkeypatch.setattr(
        "prospectforge.people_discovery.service.get_default_person_discovery_provider",
        lambda: fake,
    )
    return fake


@pytest.fixture(autouse=True)
def _fake_contact_enrichment_provider_by_default(monkeypatch):
    """Same reasoning as the fixtures above, for contact enrichment -
    keeps trigger/API-level tests independent of the seed
    contact_enrichment.csv's exact contents. Dedicated logic is tested
    directly instead (test_contact_enrichment_csv_provider.py,
    test_contact_enrichment_apollo_provider.py)."""

    from tests.fakes import FakeContactEnrichmentProvider

    fake = FakeContactEnrichmentProvider()
    monkeypatch.setattr(
        "prospectforge.contact_enrichment.service.get_default_contact_enrichment_provider",
        lambda: fake,
    )
    return fake


@pytest.fixture(autouse=True)
def _fake_rationale_provider_by_default(monkeypatch):
    """Same reasoning as the fixtures above, for qualification's
    rationale step. Dedicated JSON-parsing/validation logic is tested
    directly against rationale.py and AnthropicRationaleProvider instead
    (test_qualification_rationale.py, test_qualification_anthropic_provider.py)."""

    from tests.fakes import FakeRationaleProvider

    fake = FakeRationaleProvider()
    monkeypatch.setattr(
        "prospectforge.qualification.service.get_default_rationale_provider", lambda: fake
    )
    return fake
