"""Tests for CsvDiscoveryProvider - the active default discovery provider
(see ADR-003's addendum). Uses the real seed CSV
(prospectforge/discovery/seed_data/saas_fictional_accounts.csv), which was
deliberately built with a mix of matching, non-matching, and unmappable
rows so these tests exercise the filtering logic against real data, not a
synthetic fixture.
"""

import pytest

from prospectforge.discovery.interface import DiscoveryCriteria
from prospectforge.discovery.providers.csv_provider import CsvDiscoveryProvider
from prospectforge.icp.loader import load_icp_config
from prospectforge.discovery.criteria import criteria_from_icp


@pytest.fixture()
def provider() -> CsvDiscoveryProvider:
    return CsvDiscoveryProvider()


@pytest.fixture()
def real_icp_criteria() -> DiscoveryCriteria:
    return criteria_from_icp(load_icp_config("saas-fictional-v1"))


def test_filters_to_matching_rows_using_the_real_icp_criteria(provider, real_icp_criteria):
    page = provider.search_accounts(real_icp_criteria, page=1, per_page=100)

    names = {org.account.name for org in page.organizations if org.account}
    # the 8 rows in the seed CSV that match industry + size + geography
    assert names == {
        "Northstar Metrics",
        "Fieldglow",
        "Bramblecart",
        "Loomwork",
        "Verdant Analytics",
        "Circuiton",
        "Haloworks",
        "Quillstack",
    }


def test_excludes_wrong_industry(provider, real_icp_criteria):
    page = provider.search_accounts(real_icp_criteria, page=1, per_page=100)
    names = {org.account.name for org in page.organizations if org.account}
    assert "Ironforge Manufacturing" not in names  # Industrial Manufacturing
    assert "Gambit Gaming Systems" not in names  # Gambling & Casinos


def test_excludes_employee_count_outside_range(provider, real_icp_criteria):
    page = provider.search_accounts(real_icp_criteria, page=1, per_page=100)
    names = {org.account.name for org in page.organizations if org.account}
    assert "TinyStartup Co" not in names  # 4 employees, below range
    assert "MegaCorp Software" not in names  # 5000 employees, above range


def test_excludes_unsupported_geography(provider, real_icp_criteria):
    page = provider.search_accounts(real_icp_criteria, page=1, per_page=100)
    names = {org.account.name for org in page.organizations if org.account}
    assert "Riverside Software Solutions" not in names  # India, not a supported market


def test_row_matching_criteria_but_missing_domain_is_returned_unmappable(provider, real_icp_criteria):
    """'Unknown Ventures' matches industry/size/geography but has an empty
    domain - it should pass the criteria filter (same as a live search API
    would return it) and only get excluded at the mapping stage, which is
    a meaningfully different failure mode from being filtered out by
    criteria at all."""

    page = provider.search_accounts(real_icp_criteria, page=1, per_page=100)
    unmappable = [org for org in page.organizations if org.account is None]
    assert len(unmappable) == 1
    assert unmappable[0].raw_payload["name"] == "Unknown Ventures"
    assert "no domain" in unmappable[0].skip_reason


def test_mapped_account_fields_and_geography(provider, real_icp_criteria):
    page = provider.search_accounts(real_icp_criteria, page=1, per_page=100)
    northstar = next(org for org in page.organizations if org.account and org.account.name == "Northstar Metrics")
    assert northstar.account.domain == "northstar-metrics.com"
    assert northstar.account.industry == "Computer Software"
    assert northstar.account.employee_count == 120
    # geography holds the country alone, matched exactly against the ICP's
    # geography criterion (Step 6) - city/state stay in raw_payload only
    assert northstar.account.geography == "United States"
    assert northstar.raw_payload["city"] == "Austin"


def test_pagination_splits_results_correctly(provider, real_icp_criteria):
    page_1 = provider.search_accounts(real_icp_criteria, page=1, per_page=3)
    page_2 = provider.search_accounts(real_icp_criteria, page=2, per_page=3)

    assert len(page_1.organizations) == 3
    assert len(page_2.organizations) == 3
    assert page_1.total_entries == page_2.total_entries == 9  # 8 clean matches + 1 unmappable
    assert page_1.total_pages == page_2.total_pages == 3

    page_1_names = {org.raw_payload["name"] for org in page_1.organizations}
    page_2_names = {org.raw_payload["name"] for org in page_2.organizations}
    assert page_1_names.isdisjoint(page_2_names)  # no overlap between pages


def test_no_criteria_filtering_returns_every_row():
    provider = CsvDiscoveryProvider()
    page = provider.search_accounts(DiscoveryCriteria(), page=1, per_page=100)
    assert page.total_entries == 14  # every row in the seed CSV


def test_missing_csv_file_raises_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        CsvDiscoveryProvider(csv_path=tmp_path / "does-not-exist.csv")
