"""Tests for discovery/service.py's orchestration: per-item persistence,
the ingestion-time dedup checkpoint, unmappable-result handling, and the
result cap that bounds pagination.

Uses hand-built DiscoveryProvider stand-ins (not tests.fakes.FakeDiscoveryProvider,
which exists for trigger/API-level tests that don't care about the exact
data) so each test controls precisely what the "provider" returns.
"""

import uuid

from app.orm import AccountORM, ProviderRecordORM, RunORM
from prospectforge.discovery.interface import (
    DiscoveredOrganization,
    DiscoveryCriteria,
    DiscoveryPage,
    DiscoveryProvider,
)
from prospectforge.discovery.service import run_discovery
from prospectforge.models import Account
from prospectforge.models.enums import RunStatus


class _ScriptedProvider(DiscoveryProvider):
    """Returns a fixed sequence of pages, one per call to search_accounts,
    regardless of the page number requested - lets a test hand-script
    exactly what "Apollo" returns across a multi-page run."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = 0

    def search_accounts(self, criteria: DiscoveryCriteria, *, page: int, per_page: int) -> DiscoveryPage:
        result = self._pages[min(self.calls, len(self._pages) - 1)]
        self.calls += 1
        return result


def _org(domain: str, name: str) -> DiscoveredOrganization:
    return DiscoveredOrganization(
        account=Account(id=uuid.uuid4(), domain=domain, name=name),
        raw_payload={"name": name, "primary_domain": domain},
    )


def _bare_run(db_session) -> RunORM:
    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    return run


def test_persists_new_accounts_tagged_with_the_discovering_run(db_session):
    run = _bare_run(db_session)
    provider = _ScriptedProvider(
        [DiscoveryPage(organizations=[_org("acme.com", "Acme")], page=1, total_pages=1, total_entries=1)]
    )

    summary = run_discovery(run.id, "saas-fictional-v1", db_session, provider=provider)

    assert summary["persisted_new"] == 1
    assert summary["skipped_duplicate"] == 0

    persisted = db_session.query(AccountORM).filter_by(domain="acme.com").one()
    assert persisted.discovered_in_run_id == run.id
    assert persisted.name == "Acme"


def test_duplicate_within_the_same_page_is_deduped(db_session):
    run = _bare_run(db_session)
    provider = _ScriptedProvider(
        [
            DiscoveryPage(
                organizations=[_org("acme.com", "Acme"), _org("acme.com", "Acme (dupe)")],
                page=1,
                total_pages=1,
                total_entries=2,
            )
        ]
    )

    summary = run_discovery(run.id, "saas-fictional-v1", db_session, provider=provider)

    assert summary["persisted_new"] == 1
    assert summary["skipped_duplicate"] == 1
    assert db_session.query(AccountORM).filter_by(domain="acme.com").count() == 1


def test_duplicate_across_separate_runs_is_deduped_globally(db_session):
    """The ingestion-time dedup checkpoint is global, not per-run - a
    company already known from an earlier run shouldn't be re-persisted,
    and its discovered_in_run_id (provenance of first discovery) should
    stay pointed at the original run, not get overwritten."""

    first_run = _bare_run(db_session)
    run_discovery(
        first_run.id,
        "saas-fictional-v1",
        db_session,
        provider=_ScriptedProvider(
            [DiscoveryPage(organizations=[_org("acme.com", "Acme")], page=1, total_pages=1, total_entries=1)]
        ),
    )

    second_run = _bare_run(db_session)
    summary = run_discovery(
        second_run.id,
        "saas-fictional-v1",
        db_session,
        provider=_ScriptedProvider(
            [DiscoveryPage(organizations=[_org("acme.com", "Acme")], page=1, total_pages=1, total_entries=1)]
        ),
    )

    assert summary["persisted_new"] == 0
    assert summary["skipped_duplicate"] == 1
    persisted = db_session.query(AccountORM).filter_by(domain="acme.com").one()
    assert persisted.discovered_in_run_id == first_run.id  # unchanged


def test_unmappable_organization_is_skipped_but_still_logged_as_a_provider_record(db_session):
    run = _bare_run(db_session)
    unmappable = DiscoveredOrganization(
        account=None, raw_payload={"name": "No Domain Co"}, skip_reason="no domain in Apollo response"
    )
    provider = _ScriptedProvider(
        [DiscoveryPage(organizations=[unmappable], page=1, total_pages=1, total_entries=1)]
    )

    summary = run_discovery(run.id, "saas-fictional-v1", db_session, provider=provider)

    assert summary["skipped_unmappable"] == 1
    assert summary["persisted_new"] == 0
    record = db_session.query(ProviderRecordORM).filter_by(operation="discovery").order_by(
        ProviderRecordORM.fetched_at.desc()
    ).first()
    assert record.account_id is None
    assert record.payload["name"] == "No Domain Co"


def test_pagination_stops_at_total_pages(db_session):
    run = _bare_run(db_session)
    provider = _ScriptedProvider(
        [
            DiscoveryPage(organizations=[_org("a.com", "A")], page=1, total_pages=2, total_entries=2),
            DiscoveryPage(organizations=[_org("b.com", "B")], page=2, total_pages=2, total_entries=2),
        ]
    )

    summary = run_discovery(run.id, "saas-fictional-v1", db_session, provider=provider, max_results=1000)

    assert summary["pages_fetched"] == 2
    assert summary["persisted_new"] == 2


def test_result_cap_stops_pagination_early(db_session):
    """max_results bounds how many organizations a run will pull, even if
    more pages exist - protects free-tier quota (ADR-003) rather than
    always draining every available page."""

    run = _bare_run(db_session)
    provider = _ScriptedProvider(
        [
            DiscoveryPage(organizations=[_org("a.com", "A"), _org("b.com", "B")], page=1, total_pages=5, total_entries=500),
            DiscoveryPage(organizations=[_org("c.com", "C")], page=2, total_pages=5, total_entries=500),
        ]
    )

    summary = run_discovery(run.id, "saas-fictional-v1", db_session, provider=provider, max_results=2)

    assert summary["pages_fetched"] == 1  # never fetched page 2
    assert summary["persisted_new"] == 2
    assert provider.calls == 1
