import uuid

from app.orm import AccountORM, ContactORM, ProviderRecordORM, RunORM
from infra.retry import NonRetryableError
from prospectforge.models import Account, Contact
from prospectforge.models.enums import AccountStatus, RunStatus
from prospectforge.people_discovery.interface import (
    DiscoveredPerson,
    PersonDiscoveryPage,
    PersonDiscoveryProvider,
    PersonSearchCriteria,
)
from prospectforge.people_discovery.service import run_people_discovery


def _bare_run(db_session) -> RunORM:
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


def _researched_account(db_session, **overrides) -> AccountORM:
    defaults = dict(
        id=uuid.uuid4(),
        domain=f"{uuid.uuid4().hex[:8]}.example.com",
        name="Example Co",
        status=AccountStatus.RESEARCHED,
    )
    defaults.update(overrides)
    account = AccountORM(**defaults)
    db_session.add(account)
    db_session.flush()
    return account


def _person(account_id, name="Jane Doe", title="VP of Revenue Operations") -> DiscoveredPerson:
    return DiscoveredPerson(
        contact=Contact(id=uuid.uuid4(), account_id=account_id, name=name, title=title),
        raw_payload={"name": name, "title": title},
        matched_rule="seniority keyword 'VP' + department keyword 'Revenue'",
    )


class _PerDomainProvider(PersonDiscoveryProvider):
    def __init__(self, outcomes: dict):
        self._outcomes = outcomes  # domain -> PersonDiscoveryPage | Exception

    def search_people(self, account: Account, criteria: PersonSearchCriteria, *, page: int, per_page: int):
        outcome = self._outcomes[account.domain]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_finds_and_persists_a_matching_contact(db_session):
    run = _bare_run(db_session)
    account = _researched_account(db_session)
    provider = _PerDomainProvider(
        {account.domain: PersonDiscoveryPage(people=[_person(account.id)], page=1, total_pages=1, total_entries=1)}
    )

    summary = run_people_discovery(run.id, db_session, provider=provider)

    assert summary["accounts_with_contacts"] == 1
    assert summary["contacts_found"] == 1

    contacts = db_session.query(ContactORM).filter_by(account_id=account.id).all()
    assert len(contacts) == 1
    assert contacts[0].name == "Jane Doe"

    # Account.status is untouched by this stage - see service.py's
    # module docstring for why.
    db_session.refresh(account)
    assert account.status == AccountStatus.RESEARCHED


def test_zero_matches_is_recorded_not_an_error(db_session):
    run = _bare_run(db_session)
    account = _researched_account(db_session)
    provider = _PerDomainProvider(
        {account.domain: PersonDiscoveryPage(people=[], page=1, total_pages=1, total_entries=0)}
    )

    summary = run_people_discovery(run.id, db_session, provider=provider)

    assert summary["accounts_with_no_matches"] == 1
    assert summary["contacts_found"] == 0

    record = (
        db_session.query(ProviderRecordORM)
        .filter_by(account_id=account.id, operation="people_discovery")
        .one()
    )
    assert record.payload["matched_count"] == 0


def test_account_with_zero_matches_is_not_retried_on_a_later_run(db_session):
    """The completion marker (a ProviderRecord, not an Account.status
    value - see module docstring) must correctly prevent re-processing a
    genuinely zero-match account on a subsequent run."""

    first_run = _bare_run(db_session)
    account = _researched_account(db_session)
    empty_provider = _PerDomainProvider(
        {account.domain: PersonDiscoveryPage(people=[], page=1, total_pages=1, total_entries=0)}
    )
    run_people_discovery(first_run.id, db_session, provider=empty_provider)

    second_run = _bare_run(db_session)
    # A provider that WOULD find a contact this time - if the account gets
    # re-picked-up, this proves the resumability gate failed.
    would_find_provider = _PerDomainProvider(
        {account.domain: PersonDiscoveryPage(people=[_person(account.id)], page=1, total_pages=1, total_entries=1)}
    )
    summary = run_people_discovery(second_run.id, db_session, provider=would_find_provider)

    assert summary["accounts_evaluated"] == 0  # already-searched account correctly skipped


def test_account_with_a_failed_search_is_retried_on_a_later_run(db_session):
    """The mirror image of the test above: a FAILED attempt must NOT
    create a completion marker, so it's correctly retried later."""

    first_run = _bare_run(db_session)
    account = _researched_account(db_session)
    failing_provider = _PerDomainProvider({account.domain: NonRetryableError("401 unauthorized")})
    summary_1 = run_people_discovery(first_run.id, db_session, provider=failing_provider)
    assert summary_1["search_failed"] == 1

    second_run = _bare_run(db_session)
    succeeding_provider = _PerDomainProvider(
        {account.domain: PersonDiscoveryPage(people=[_person(account.id)], page=1, total_pages=1, total_entries=1)}
    )
    summary_2 = run_people_discovery(second_run.id, db_session, provider=succeeding_provider)

    assert summary_2["accounts_evaluated"] == 1  # picked up again
    assert summary_2["contacts_found"] == 1


def test_unmappable_person_is_logged_but_not_counted_as_a_found_contact(db_session):
    run = _bare_run(db_session)
    account = _researched_account(db_session)
    unmappable = DiscoveredPerson(
        contact=None,
        raw_payload={"title": "VP of Sales"},
        skip_reason="no name in CSV row",
        matched_rule="seniority keyword 'VP' + department keyword 'Sales'",
    )
    provider = _PerDomainProvider(
        {account.domain: PersonDiscoveryPage(people=[unmappable], page=1, total_pages=1, total_entries=1)}
    )

    summary = run_people_discovery(run.id, db_session, provider=provider)

    assert summary["contacts_found"] == 0
    assert summary["accounts_with_no_matches"] == 1  # zero USABLE contacts found
    assert db_session.query(ContactORM).filter_by(account_id=account.id).count() == 0


def test_only_processes_researched_accounts(db_session):
    run = _bare_run(db_session)
    raw_account = _researched_account(db_session, status=AccountStatus.RAW)
    provider = _PerDomainProvider({})

    summary = run_people_discovery(run.id, db_session, provider=provider)

    assert summary["accounts_evaluated"] == 0
    db_session.refresh(raw_account)
    assert raw_account.status == AccountStatus.RAW


def test_multiple_matching_contacts_are_all_persisted(db_session):
    """The 'buying committee' case - see the roadmap's explicit
    requirement not to silently pick just one candidate."""

    run = _bare_run(db_session)
    account = _researched_account(db_session)
    people = [_person(account.id, name="Jane Doe"), _person(account.id, name="Carlos Mendez")]
    provider = _PerDomainProvider(
        {account.domain: PersonDiscoveryPage(people=people, page=1, total_pages=1, total_entries=2)}
    )

    summary = run_people_discovery(run.id, db_session, provider=provider)

    assert summary["contacts_found"] == 2
    contacts = db_session.query(ContactORM).filter_by(account_id=account.id).all()
    assert {c.name for c in contacts} == {"Jane Doe", "Carlos Mendez"}
