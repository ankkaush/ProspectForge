"""Tests for CsvPersonDiscoveryProvider, using the real seed data
(prospectforge/people_discovery/seed_data/contacts.csv) and the real
primary-buyer-v1 persona - deliberately built with a mix of matching and
non-matching titles, a multi-contact "buying committee" company
(northstar-metrics.com), and one unmappable row (missing name), so these
tests exercise real filtering rather than a synthetic fixture.
"""

import pytest

from prospectforge.models import Account
from prospectforge.people_discovery.interface import PersonSearchCriteria
from prospectforge.people_discovery.providers.csv_provider import CsvPersonDiscoveryProvider
from prospectforge.persona.loader import load_persona_config


@pytest.fixture()
def provider() -> CsvPersonDiscoveryProvider:
    return CsvPersonDiscoveryProvider()


@pytest.fixture()
def criteria() -> PersonSearchCriteria:
    persona = load_persona_config("primary-buyer-v1")
    return PersonSearchCriteria(
        seniority_keywords=persona.seniority_keywords,
        department_keywords=persona.department_keywords,
    )


def _account(domain: str) -> Account:
    return Account(domain=domain, name="Test Co")


def test_returns_only_persona_matching_contacts(provider, criteria):
    page = provider.search_people(_account("fieldglow.io"), criteria, page=1, per_page=25)

    names = {p.contact.name for p in page.people if p.contact}
    assert names == {"Priya Shah"}  # Director of Sales Operations matches; Marketing Coordinator doesn't


def test_non_matching_titles_are_excluded_entirely(provider, criteria):
    page = provider.search_people(_account("bramblecart.com"), criteria, page=1, per_page=25)
    names = {p.contact.name for p in page.people if p.contact}
    assert "Lucia Fernandez" not in names  # Customer Support Specialist


def test_company_with_multiple_matching_contacts_returns_all_of_them(provider, criteria):
    """northstar-metrics.com is the seed data's deliberate 'buying
    committee' case - two people there both match the persona, and the
    provider should surface both, not silently pick one (the roadmap's
    explicit requirement for this step)."""

    page = provider.search_people(_account("northstar-metrics.com"), criteria, page=1, per_page=25)
    names = {p.contact.name for p in page.people if p.contact}
    assert names == {"Jane Doe", "Carlos Mendez"}


def test_row_matching_persona_but_missing_name_is_unmappable(provider, criteria):
    """quillstack.io's blank-name row has a title that matches the
    persona (VP of Revenue Operations) - it should pass the persona filter
    and only fail at the mapping stage, a meaningfully different outcome
    from simply not matching the persona at all."""

    page = provider.search_people(_account("quillstack.io"), criteria, page=1, per_page=25)
    unmappable = [p for p in page.people if p.contact is None]
    assert len(unmappable) == 1
    assert "no name" in unmappable[0].skip_reason
    assert unmappable[0].matched_rule is not None  # it DID match, just couldn't be identified


def test_company_with_no_matching_contacts_returns_empty_page(provider, criteria):
    page = provider.search_people(_account("does-not-exist.com"), criteria, page=1, per_page=25)
    assert page.people == []
    assert page.total_entries == 0


def test_matched_rule_is_recorded_per_contact(provider, criteria):
    page = provider.search_people(_account("haloworks.com"), criteria, page=1, per_page=25)
    rossi = next(p for p in page.people if p.contact and p.contact.name == "Maria Rossi")
    assert "SVP" in rossi.matched_rule
    assert "Sales" in rossi.matched_rule


def test_contact_account_id_matches_the_searched_account(provider, criteria):
    account = _account("fieldglow.io")
    page = provider.search_people(account, criteria, page=1, per_page=25)
    assert page.people[0].contact.account_id == account.id


def test_missing_csv_file_raises_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        CsvPersonDiscoveryProvider(csv_path=tmp_path / "does-not-exist.csv")
