"""Test doubles for the provider ports. Living in tests/, not in
prospectforge/ - these are not a second production implementation (the
architecture deliberately has exactly one real DiscoveryProvider, Apollo;
see ADR-005), they're what ADR-005 calls out explicitly as the payoff of
the port/adapter boundary: pipeline logic can be exercised against a fake
without any real network call.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from prospectforge.discovery.interface import (
    DiscoveredOrganization,
    DiscoveryCriteria,
    DiscoveryPage,
    DiscoveryProvider,
)
from prospectforge.contact_enrichment.interface import ContactEnrichmentProvider, ContactEnrichmentResult
from prospectforge.enrichment.interface import EnrichmentProvider, EnrichmentResult
from prospectforge.models import Account, ConfidenceLevel, Contact, Evidence, EvidenceSourceType
from prospectforge.people_discovery.interface import (
    DiscoveredPerson,
    PersonDiscoveryPage,
    PersonDiscoveryProvider,
    PersonSearchCriteria,
)
from prospectforge.qualification.interface import RationaleContext, RationaleProvider, RationaleResult
from prospectforge.research.interface import ResearchProvider, ResearchResult


class FakeDiscoveryProvider(DiscoveryProvider):
    """Returns a fixed, small set of accounts on the first page and an
    empty second page - enough to exercise dedup, persistence, and the
    pagination-termination logic without a real Apollo response."""

    def __init__(self, organizations: Optional[List[DiscoveredOrganization]] = None):
        self.organizations = organizations or self._default_organizations()
        self.calls: List[tuple] = []  # (criteria, page, per_page) - for assertions

    def search_accounts(
        self, criteria: DiscoveryCriteria, *, page: int, per_page: int
    ) -> DiscoveryPage:
        self.calls.append((criteria, page, per_page))
        if page == 1:
            orgs = self.organizations
        else:
            orgs = []
        return DiscoveryPage(
            organizations=orgs,
            page=page,
            total_pages=1,
            total_entries=len(self.organizations),
        )

    @staticmethod
    def _default_organizations() -> List[DiscoveredOrganization]:
        # Domains get a random suffix so each test (and the autouse fixture
        # that builds a fresh FakeDiscoveryProvider per test) sees globally
        # unique accounts - the ingestion-time dedup checkpoint is
        # deliberately global-across-runs (see discovery/service.py), so
        # reusing fixed domain names across tests would make later tests
        # see the earlier tests' accounts as duplicates.
        suffix = uuid.uuid4().hex[:8]
        return [
            DiscoveredOrganization(
                account=Account(
                    id=uuid.uuid4(),
                    domain=f"northstar-metrics-{suffix}.com",
                    name="Northstar Metrics",
                    industry="Computer Software",
                    employee_count=120,
                    geography="United States",
                ),
                raw_payload={
                    "name": "Northstar Metrics",
                    "primary_domain": f"northstar-metrics-{suffix}.com",
                },
            ),
            DiscoveredOrganization(
                account=Account(
                    id=uuid.uuid4(),
                    domain=f"fieldglow-{suffix}.io",
                    name="Fieldglow",
                    industry="SaaS",
                    employee_count=80,
                    geography="Germany",
                ),
                raw_payload={"name": "Fieldglow", "primary_domain": f"fieldglow-{suffix}.io"},
            ),
        ]


class FakeEnrichmentProvider(EnrichmentProvider):
    """Returns a fixed, successful enrichment result for every account -
    enough to exercise the enrichment stage's persistence and status
    transitions in trigger/API-level tests without a real Apollo call.
    Dedicated enrichment logic (growth-signal thresholds, no-data
    handling, retry classification) is tested directly against
    ApolloEnrichmentProvider instead - see test_enrichment_apollo_provider.py."""

    def __init__(self, result: Optional[EnrichmentResult] = None):
        self.result = result or EnrichmentResult(
            found=True,
            tech_stack=["Salesforce"],
            funding_stage="Series B",
            growth_signal="hiring",
            raw_payload={"technology_names": ["Salesforce"]},
        )
        self.calls: List[Account] = []

    def enrich_account(self, account: Account) -> EnrichmentResult:
        self.calls.append(account)
        return self.result


class FakeResearchProvider(ResearchProvider):
    """Returns one fixed, sourced evidence item for every account -
    enough to exercise the research stage's persistence and status
    transitions in trigger/API-level tests without a real Claude API call.
    Dedicated research logic (JSON parsing, the source-verification
    cross-check, retry-on-malformed-JSON) is tested directly against
    extractor.py and AnthropicWebSearchResearchProvider instead."""

    def __init__(self, result: Optional[ResearchResult] = None):
        self.result = result or ResearchResult(
            evidence=[
                Evidence(
                    account_id=uuid.uuid4(),  # overwritten per-call below
                    claim="Posted 3 open engineering roles in the last 30 days",
                    source_type=EvidenceSourceType.AI_INFERRED,
                    source_url="https://example.com/careers",
                    confidence=ConfidenceLevel.MEDIUM,
                )
            ],
            verified_urls=["https://example.com/careers"],
        )
        self.calls: List[Account] = []

    def research_account(self, account: Account) -> ResearchResult:
        self.calls.append(account)
        # Evidence.account_id must match the account actually being
        # researched, not whatever placeholder the default result used.
        evidence = [e.model_copy(update={"account_id": account.id}) for e in self.result.evidence]
        return self.result.model_copy(update={"evidence": evidence})


class FakePersonDiscoveryProvider(PersonDiscoveryProvider):
    """Returns one fixed, persona-matching candidate contact for every
    account - enough to exercise the people-discovery stage's persistence
    in trigger/API-level tests without a real Apollo call. Dedicated
    matching/filtering logic is tested directly against
    CsvPersonDiscoveryProvider and ApolloPersonDiscoveryProvider instead."""

    def __init__(self, people: Optional[List[DiscoveredPerson]] = None):
        self._template_people = people
        self.calls: List[Account] = []

    def search_people(
        self, account: Account, criteria: PersonSearchCriteria, *, page: int, per_page: int
    ) -> PersonDiscoveryPage:
        self.calls.append(account)
        if page > 1:
            return PersonDiscoveryPage(people=[], page=page, total_pages=1, total_entries=1)

        people = self._template_people or [
            DiscoveredPerson(
                contact=Contact(
                    id=uuid.uuid4(),
                    account_id=account.id,
                    name="Jane Doe",
                    title="VP of Revenue Operations",
                    department="Revenue Operations",
                ),
                raw_payload={"name": "Jane Doe", "title": "VP of Revenue Operations"},
                matched_rule="seniority keyword 'VP' + department keyword 'Revenue'",
            )
        ]
        # account_id must match the account actually being searched, not
        # whatever placeholder a caller-supplied template used.
        people = [
            p.model_copy(
                update={"contact": p.contact.model_copy(update={"account_id": account.id})}
            )
            if p.contact
            else p
            for p in people
        ]
        return PersonDiscoveryPage(people=people, page=page, total_pages=1, total_entries=len(people))


class FakeContactEnrichmentProvider(ContactEnrichmentProvider):
    """Returns a fixed, successful enrichment result for every contact -
    enough to exercise the contact-enrichment stage's persistence and
    status transitions in trigger/API-level tests without a real Apollo
    call. Dedicated logic (email format validation, no-data handling,
    retry classification) is tested directly against
    CsvContactEnrichmentProvider and ApolloContactEnrichmentProvider
    instead."""

    def __init__(self, result: Optional[ContactEnrichmentResult] = None):
        self.result = result or ContactEnrichmentResult(
            found=True,
            email="jane.doe@example.com",
            email_confidence="verified",
            seniority="vp",
            linkedin_url="https://linkedin.com/in/janedoe",
            raw_payload={"email": "jane.doe@example.com"},
        )
        self.calls: List[Contact] = []

    def enrich_contact(self, contact: Contact, account: Account) -> ContactEnrichmentResult:
        self.calls.append(contact)
        return self.result


class FakeRationaleProvider(RationaleProvider):
    """Returns a fixed rationale string for every call - enough to
    exercise the qualification stage's persistence in trigger/API-level
    tests without a real Claude call. Dedicated JSON-parsing/validation
    logic is tested directly against rationale.py and
    AnthropicRationaleProvider instead."""

    def __init__(self, result: Optional[RationaleResult] = None):
        self.result = result or RationaleResult(
            rationale_text="Fake rationale: strong fit with a verified contact.",
            dropped_statement_count=0,
        )
        self.calls: List[RationaleContext] = []

    def generate_rationale(self, context: RationaleContext) -> RationaleResult:
        self.calls.append(context)
        return self.result
