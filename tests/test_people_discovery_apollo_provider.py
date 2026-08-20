"""Tests for ApolloPersonDiscoveryProvider - mocked via httpx.MockTransport,
same discipline as the Apollo discovery/enrichment provider tests. Field
names follow Apollo's public docs (verified live only to the extent that
the endpoint path itself returns a real, structured 403 rather than a
404 - see this provider's module docstring); the mapping is best-effort
until real access exists.
"""

import httpx
import pytest

from infra.retry import NonRetryableError, RetryableError
from prospectforge.models import Account
from prospectforge.people_discovery.interface import PersonSearchCriteria
from prospectforge.people_discovery.providers.apollo import ApolloPersonDiscoveryProvider


def _provider(handler) -> ApolloPersonDiscoveryProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ApolloPersonDiscoveryProvider(api_key="test-apollo-key", client=client)


def _account() -> Account:
    return Account(domain="northstar-metrics.com", name="Northstar Metrics")


def _criteria() -> PersonSearchCriteria:
    return PersonSearchCriteria(seniority_keywords=["VP", "Director"], department_keywords=["Sales", "Revenue"])


def test_maps_matching_people_and_scopes_to_the_account_domain():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-apollo-key"
        import json

        body = json.loads(request.content)
        assert body["q_organization_domains_list"] == ["northstar-metrics.com"]
        return httpx.Response(
            200,
            json={
                "people": [
                    {"first_name": "Jane", "last_name_obfuscated": "D.", "title": "VP of Revenue Operations"},
                    {"first_name": "Tom", "last_name_obfuscated": "L.", "title": "Office Manager"},
                ],
                "total_entries": 2,
                "total_pages": 1,
            },
        )

    page = _provider(handler).search_people(_account(), _criteria(), page=1, per_page=25)

    names = {p.contact.name for p in page.people if p.contact}
    assert names == {"Jane D."}  # Office Manager filtered out by persona matching


def test_person_with_no_usable_name_is_unmappable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"people": [{"title": "VP of Sales"}], "total_entries": 1, "total_pages": 1},
        )

    criteria = PersonSearchCriteria(seniority_keywords=["VP"], department_keywords=["Sales"])
    page = _provider(handler).search_people(_account(), criteria, page=1, per_page=25)

    assert len(page.people) == 1
    assert page.people[0].contact is None
    assert "no usable name" in page.people[0].skip_reason


def test_empty_result_set_is_handled_without_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"people": [], "total_entries": 0, "total_pages": 1})

    page = _provider(handler).search_people(_account(), _criteria(), page=1, per_page=25)
    assert page.people == []


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_server_and_rate_limit_errors_are_retryable(status_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="try again later")

    with pytest.raises(RetryableError):
        _provider(handler).search_people(_account(), _criteria(), page=1, per_page=25)


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_client_and_auth_errors_are_non_retryable(status_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="bad request")

    with pytest.raises(NonRetryableError):
        _provider(handler).search_people(_account(), _criteria(), page=1, per_page=25)


def test_the_confirmed_real_403_payload_is_non_retryable():
    """The actual, live response this project received when re-checking
    Apollo people-search access at the start of this step - confirms our
    classification treats it correctly (a plan-access error, not a
    transient one worth retrying)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": "The api/v1/mixed_people/search API is not included in your "
                "Free plan and is not accessible, even with a master key.",
                "error_code": "API_INACCESSIBLE",
            },
        )

    with pytest.raises(NonRetryableError):
        _provider(handler).search_people(_account(), _criteria(), page=1, per_page=25)


def test_missing_api_key_is_rejected_at_construction():
    with pytest.raises(ValueError):
        ApolloPersonDiscoveryProvider(api_key="")
