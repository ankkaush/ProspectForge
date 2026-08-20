"""Tests for ApolloEnrichmentProvider - the one file allowed to know
Apollo's organization-enrichment request/response shape. Field names and
the growth-signal derivation are grounded in a real live response (see
this step's notes) captured against stripe.com, reused here as a fixture
so these tests don't need network access or a real API key.
"""

import httpx
import pytest

from infra.retry import NonRetryableError, RetryableError
from prospectforge.enrichment.providers.apollo import ApolloEnrichmentProvider
from prospectforge.models import Account


def _provider(handler) -> ApolloEnrichmentProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ApolloEnrichmentProvider(api_key="test-apollo-key", client=client)


def _account(domain="northstar-metrics.com") -> Account:
    return Account(domain=domain, name="Northstar Metrics")


# --- successful mapping, using a realistic captured shape ------------------

def test_maps_a_realistic_successful_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-apollo-key"
        assert request.url.params["domain"] == "northstar-metrics.com"
        return httpx.Response(
            200,
            json={
                "organization": {
                    "name": "Northstar Metrics",
                    "technology_names": ["Salesforce", "Slack", "AWS"],
                    "latest_funding_stage": "Series B",
                    "organization_headcount_twelve_month_growth": 0.19,
                    "estimated_num_employees": 120,
                }
            },
        )

    result = _provider(handler).enrich_account(_account())

    assert result.found is True
    assert result.tech_stack == ["Salesforce", "Slack", "AWS"]
    assert result.funding_stage == "Series B"
    assert result.growth_signal == "hiring"  # 19% >= 15% threshold
    assert result.raw_payload["name"] == "Northstar Metrics"


@pytest.mark.parametrize(
    "growth,expected_signal",
    [
        (0.30, "hiring"),
        (0.15, "hiring"),  # exactly at threshold
        (0.10, "scaling"),
        (0.05, "scaling"),  # exactly at threshold
        (0.01, None),
        (0.0, None),
        (-0.05, None),  # shrinking headcount
    ],
)
def test_growth_signal_thresholds(growth, expected_signal):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "organization": {
                    "name": "X",
                    "organization_headcount_twelve_month_growth": growth,
                }
            },
        )

    result = _provider(handler).enrich_account(_account())
    assert result.growth_signal == expected_signal


def test_missing_growth_field_yields_no_signal():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organization": {"name": "X"}})

    result = _provider(handler).enrich_account(_account())
    assert result.growth_signal is None
    assert result.tech_stack is None
    assert result.funding_stage is None


# --- "no data" is success, not an error -------------------------------------

def test_empty_response_body_is_found_false_not_an_error():
    """This is the exact shape a real fictional domain produced live:
    HTTP 200, empty body - Apollo simply has no record for this domain."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    result = _provider(handler).enrich_account(_account())
    assert result.found is False
    assert result.tech_stack is None


def test_null_organization_key_is_found_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organization": None})

    result = _provider(handler).enrich_account(_account())
    assert result.found is False


# --- failure classification -------------------------------------------------

@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_server_and_rate_limit_errors_are_retryable(status_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="try again later")

    with pytest.raises(RetryableError):
        _provider(handler).enrich_account(_account())


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_client_and_auth_errors_are_non_retryable(status_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="bad request")

    with pytest.raises(NonRetryableError):
        _provider(handler).enrich_account(_account())


def test_network_timeout_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    with pytest.raises(RetryableError):
        _provider(handler).enrich_account(_account())


def test_missing_api_key_is_rejected_at_construction():
    with pytest.raises(ValueError):
        ApolloEnrichmentProvider(api_key="")
