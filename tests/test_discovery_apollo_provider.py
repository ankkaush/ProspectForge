"""Tests for ApolloDiscoveryProvider - the one file allowed to know
Apollo's request/response shape. Uses httpx.MockTransport so these run
with no real network access and no API key, per the roadmap's "provider
unit tests against recorded/mocked Apollo responses."
"""

import json

import httpx
import pytest

from app.orm import RunORM
from infra.retry import NonRetryableError, RetryableError, call_with_retry
from prospectforge.discovery.interface import DiscoveryCriteria
from prospectforge.discovery.providers.apollo import ApolloDiscoveryProvider
from prospectforge.models.enums import CallStatus, RunStatus


def _provider(handler) -> ApolloDiscoveryProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ApolloDiscoveryProvider(api_key="test-apollo-key", client=client)


def _criteria() -> DiscoveryCriteria:
    return DiscoveryCriteria(
        industries=["Computer Software"],
        employee_count_min=50,
        employee_count_max=500,
        geographies=["United States"],
    )


# --- successful mapping --------------------------------------------------

def test_maps_a_realistic_successful_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-apollo-key"
        body = json.loads(request.content)
        assert body["q_organization_keyword_tags"] == ["Computer Software"]
        assert body["organization_num_employees_ranges"] == ["50,500"]
        assert body["organization_locations"] == ["United States"]
        return httpx.Response(
            200,
            json={
                "organizations": [
                    {
                        "name": "Northstar Metrics",
                        "primary_domain": "northstar-metrics.com",
                        "industry": "Computer Software",
                        "estimated_num_employees": 120,
                        "city": "Austin",
                        "state": "Texas",
                        "country": "United States",
                    }
                ],
                "pagination": {"page": 1, "total_pages": 1, "total_entries": 1},
            },
        )

    page = _provider(handler).search_accounts(_criteria(), page=1, per_page=100)

    assert page.total_pages == 1
    assert page.total_entries == 1
    assert len(page.organizations) == 1

    org = page.organizations[0]
    assert org.skip_reason is None
    assert org.account.domain == "northstar-metrics.com"
    assert org.account.name == "Northstar Metrics"
    assert org.account.industry == "Computer Software"
    assert org.account.employee_count == 120
    # geography holds the country alone, matched exactly against the ICP's
    # geography criterion (Step 6) - city/state stay in raw_payload only
    assert org.account.geography == "United States"
    assert org.raw_payload["city"] == "Austin"
    assert org.raw_payload["name"] == "Northstar Metrics"  # verbatim payload kept


def test_derives_domain_from_website_url_when_primary_domain_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "organizations": [
                    {"name": "Fieldglow", "website_url": "https://fieldglow.io/about"}
                ],
                "pagination": {"page": 1, "total_pages": 1, "total_entries": 1},
            },
        )

    page = _provider(handler).search_accounts(_criteria(), page=1, per_page=100)
    assert page.organizations[0].account.domain == "fieldglow.io"


def test_skips_organization_with_no_domain_at_all():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "organizations": [{"name": "Mystery Co"}],
                "pagination": {"page": 1, "total_pages": 1, "total_entries": 1},
            },
        )

    page = _provider(handler).search_accounts(_criteria(), page=1, per_page=100)
    org = page.organizations[0]
    assert org.account is None
    assert "no domain" in org.skip_reason
    assert org.raw_payload["name"] == "Mystery Co"  # kept even though unmapped


def test_empty_result_set_is_handled_without_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"organizations": [], "pagination": {"page": 1, "total_pages": 1, "total_entries": 0}},
        )

    page = _provider(handler).search_accounts(_criteria(), page=1, per_page=100)
    assert page.organizations == []
    assert page.total_entries == 0


# --- pagination ------------------------------------------------------------

def test_pagination_across_two_pages():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        page = body["page"]
        name = "Page One Co" if page == 1 else "Page Two Co"
        return httpx.Response(
            200,
            json={
                "organizations": [{"name": name, "primary_domain": f"{name.lower().replace(' ', '')}.com"}],
                "pagination": {"page": page, "total_pages": 2, "total_entries": 2},
            },
        )

    provider = _provider(handler)
    page_1 = provider.search_accounts(_criteria(), page=1, per_page=100)
    page_2 = provider.search_accounts(_criteria(), page=2, per_page=100)

    assert page_1.organizations[0].account.name == "Page One Co"
    assert page_2.organizations[0].account.name == "Page Two Co"
    assert page_1.total_pages == page_2.total_pages == 2


# --- failure classification: retryable vs non-retryable --------------------

@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_server_and_rate_limit_errors_are_retryable(status_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="try again later")

    with pytest.raises(RetryableError):
        _provider(handler).search_accounts(_criteria(), page=1, per_page=100)


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_client_and_auth_errors_are_non_retryable(status_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="bad request")

    with pytest.raises(NonRetryableError):
        _provider(handler).search_accounts(_criteria(), page=1, per_page=100)


def test_malformed_json_response_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="this is not json{{{")

    with pytest.raises(RetryableError):
        _provider(handler).search_accounts(_criteria(), page=1, per_page=100)


def test_network_timeout_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    with pytest.raises(RetryableError):
        _provider(handler).search_accounts(_criteria(), page=1, per_page=100)


def test_missing_api_key_is_rejected_at_construction():
    with pytest.raises(ValueError):
        ApolloDiscoveryProvider(api_key="")


# --- integration with the shared retry utility ------------------------------

def test_rate_limit_then_success_recovers_via_call_with_retry(db_session):
    """Exercises the real ApolloDiscoveryProvider through infra.retry's
    call_with_retry - the actual path discovery/service.py uses, not just
    the provider in isolation."""

    run = RunORM(icp_config_id="saas-fictional-v1", status=RunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(
            200,
            json={
                "organizations": [{"name": "Recovered Co", "primary_domain": "recovered.co"}],
                "pagination": {"page": 1, "total_pages": 1, "total_entries": 1},
            },
        )

    provider = _provider(handler)
    page = call_with_retry(
        lambda: provider.search_accounts(_criteria(), page=1, per_page=100),
        session=db_session,
        run_id=run.id,
        provider="apollo",
        operation="discovery",
        base_delay_seconds=0.01,
        sleep=lambda _seconds: None,
    )

    assert page.organizations[0].account.domain == "recovered.co"
    assert attempts["n"] == 2

    from app.orm import ExternalCallAttemptORM

    logged = (
        db_session.query(ExternalCallAttemptORM).filter_by(run_id=run.id).all()
    )
    assert [a.status for a in logged] == [CallStatus.FAILED_RETRYABLE, CallStatus.SUCCESS]
