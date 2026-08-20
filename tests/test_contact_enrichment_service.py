import uuid

from app.orm import AccountORM, ContactORM, ExternalCallAttemptORM, ProviderRecordORM, RunORM
from infra.retry import NonRetryableError
from prospectforge.contact_enrichment.interface import ContactEnrichmentProvider, ContactEnrichmentResult
from prospectforge.contact_enrichment.service import run_contact_enrichment
from prospectforge.models import Account, Contact
from prospectforge.models.enums import ContactStatus, RunStatus


def _bare_run(db_session) -> RunORM:
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


def _account_with_contact(db_session, contact_status=ContactStatus.DISCOVERED) -> ContactORM:
    account = AccountORM(id=uuid.uuid4(), domain=f"{uuid.uuid4().hex[:8]}.example.com", name="Example Co")
    db_session.add(account)
    db_session.flush()
    contact = ContactORM(
        id=uuid.uuid4(), account_id=account.id, name="Jane Doe", title="VP of Sales", status=contact_status
    )
    db_session.add(contact)
    db_session.flush()
    return contact


class _PerNameProvider(ContactEnrichmentProvider):
    def __init__(self, outcomes: dict):
        self._outcomes = outcomes  # contact name -> ContactEnrichmentResult | Exception

    def enrich_contact(self, contact: Contact, account: Account) -> ContactEnrichmentResult:
        outcome = self._outcomes[contact.name]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_enriches_a_discovered_contact_and_transitions_to_enriched(db_session):
    run = _bare_run(db_session)
    contact = _account_with_contact(db_session)
    provider = _PerNameProvider(
        {
            "Jane Doe": ContactEnrichmentResult(
                found=True, email="jane@example.com", email_confidence="verified", seniority="vp"
            )
        }
    )

    summary = run_contact_enrichment(run.id, db_session, provider=provider)

    assert summary["enriched"] == 1
    db_session.refresh(contact)
    assert contact.status == ContactStatus.ENRICHED
    assert contact.email == "jane@example.com"
    assert contact.email_confidence == "verified"


def test_no_data_found_still_transitions_to_enriched(db_session):
    run = _bare_run(db_session)
    contact = _account_with_contact(db_session)
    provider = _PerNameProvider({"Jane Doe": ContactEnrichmentResult(found=False)})

    summary = run_contact_enrichment(run.id, db_session, provider=provider)

    assert summary["no_data_found"] == 1
    db_session.refresh(contact)
    assert contact.status == ContactStatus.ENRICHED
    assert contact.email is None


def test_invalid_email_is_counted_separately_and_not_stored(db_session):
    run = _bare_run(db_session)
    contact = _account_with_contact(db_session)
    provider = _PerNameProvider(
        {"Jane Doe": ContactEnrichmentResult(found=True, email=None, email_confidence="invalid")}
    )

    summary = run_contact_enrichment(run.id, db_session, provider=provider)

    assert summary["invalid_email"] == 1
    db_session.refresh(contact)
    assert contact.email is None
    assert contact.email_confidence == "invalid"


def test_one_contact_failing_does_not_block_the_rest(db_session):
    run = _bare_run(db_session)
    contact_a = _account_with_contact(db_session)
    contact_a.name = "Contact A"
    account_b = AccountORM(id=uuid.uuid4(), domain=f"{uuid.uuid4().hex[:8]}.example.com", name="Co B")
    db_session.add(account_b)
    db_session.flush()
    contact_b = ContactORM(
        id=uuid.uuid4(), account_id=account_b.id, name="Contact B", status=ContactStatus.DISCOVERED
    )
    db_session.add(contact_b)
    db_session.flush()

    provider = _PerNameProvider(
        {
            "Contact A": NonRetryableError("401 unauthorized"),
            "Contact B": ContactEnrichmentResult(found=True, email="b@example.com", email_confidence="verified"),
        }
    )

    summary = run_contact_enrichment(run.id, db_session, provider=provider)

    assert summary["enrichment_failed"] == 1
    assert summary["enriched"] == 1

    db_session.refresh(contact_a)
    db_session.refresh(contact_b)
    assert contact_a.status == ContactStatus.ENRICHMENT_FAILED
    assert contact_b.status == ContactStatus.ENRICHED


def test_only_processes_discovered_contacts(db_session):
    run = _bare_run(db_session)
    already_enriched = _account_with_contact(db_session, contact_status=ContactStatus.ENRICHED)
    provider = _PerNameProvider({})

    summary = run_contact_enrichment(run.id, db_session, provider=provider)

    assert summary["evaluated"] == 0
    db_session.refresh(already_enriched)
    assert already_enriched.status == ContactStatus.ENRICHED


def test_a_contact_stuck_at_enrichment_failed_gets_retried_and_can_succeed(db_session):
    """Step 19's idempotency-review fix: contact enrichment had the same
    orphaning bug found in account enrichment and research - the query
    originally only looked at DISCOVERED, never ENRICHMENT_FAILED, even
    though nothing prevents a failed contact from being retried."""

    run = _bare_run(db_session)
    contact = _account_with_contact(db_session, contact_status=ContactStatus.ENRICHMENT_FAILED)
    provider = _PerNameProvider(
        {"Jane Doe": ContactEnrichmentResult(found=True, email="jane@example.com", email_confidence="verified")}
    )

    summary = run_contact_enrichment(run.id, db_session, provider=provider)

    assert summary["evaluated"] == 1
    assert summary["enriched"] == 1
    db_session.refresh(contact)
    assert contact.status == ContactStatus.ENRICHED
    assert contact.email == "jane@example.com"


def test_an_erased_contact_is_never_reprocessed(db_session):
    """Step 20's GDPR erasure fix, verified from the other direction: a
    contact at ContactStatus.ERASED must never be picked up again - if it
    were, this same run's Step 19 orphan-retry fix would silently
    repopulate the erased email, undoing the erasure."""

    run = _bare_run(db_session)
    erased_contact = _account_with_contact(db_session, contact_status=ContactStatus.ERASED)
    provider = _PerNameProvider({})

    summary = run_contact_enrichment(run.id, db_session, provider=provider)

    assert summary["evaluated"] == 0
    db_session.refresh(erased_contact)
    assert erased_contact.status == ContactStatus.ERASED


def test_raw_payload_persisted_as_provider_record(db_session):
    run = _bare_run(db_session)
    contact = _account_with_contact(db_session)
    provider = _PerNameProvider(
        {
            "Jane Doe": ContactEnrichmentResult(
                found=True, email="jane@example.com", raw_payload={"email": "jane@example.com", "status": "verified"}
            )
        }
    )

    run_contact_enrichment(run.id, db_session, provider=provider)

    record = (
        db_session.query(ProviderRecordORM)
        .filter_by(contact_id=contact.id, operation="contact_enrichment")
        .one()
    )
    assert record.payload["email"] == "jane@example.com"


def test_audit_trail_labels_the_actually_configured_provider_not_a_hardcoded_apollo(db_session):
    """Step 26's finding - same fix as discovery/service.py's equivalent
    test; see that test's docstring for the full story."""

    run = _bare_run(db_session)
    contact = _account_with_contact(db_session)
    provider = _PerNameProvider({"Jane Doe": ContactEnrichmentResult(found=True, email="jane@example.com")})

    run_contact_enrichment(run.id, db_session, provider=provider)

    attempt = db_session.query(ExternalCallAttemptORM).filter_by(run_id=run.id).one()
    assert attempt.provider == "csv"

    record = db_session.query(ProviderRecordORM).filter_by(contact_id=contact.id).one()
    assert record.provider == "csv"
