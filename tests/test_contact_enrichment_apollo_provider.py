import uuid

import httpx
import pytest

from infra.retry import NonRetryableError, RetryableError
from prospectforge.contact_enrichment.providers.apollo import ApolloContactEnrichmentProvider
from prospectforge.models import Account, Contact


def _provider(handler) -> ApolloContactEnrichmentProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ApolloContactEnrichmentProvider(api_key="test-apollo-key", client=client)


def _contact(name="Jane Doe") -> Contact:
    return Contact(id=uuid.uuid4(), account_id=uuid.uuid4(), name=name)


def _account() -> Account:
    return Account(domain="northstar-metrics.com", name="Northstar Metrics")


def test_maps_a_successful_response_with_verified_email():
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        assert body["first_name"] == "Jane"
        assert body["last_name"] == "Doe"
        assert body["domain"] == "northstar-metrics.com"
        assert body["reveal_personal_emails"] is False
        return httpx.Response(
            200,
            json={
                "person": {
                    "email": "jane.doe@northstar-metrics.com",
                    "email_status": "verified",
                    "seniority": "vp",
                    "linkedin_url": "https://linkedin.com/in/janedoe",
                }
            },
        )

    result = _provider(handler).enrich_contact(_contact(), _account())

    assert result.found is True
    assert result.email == "jane.doe@northstar-metrics.com"
    assert result.email_confidence == "verified"
    assert result.linkedin_url == "https://linkedin.com/in/janedoe"


def test_malformed_email_from_apollo_is_invalidated_not_trusted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"person": {"email": "not-a-real-email", "email_status": "verified"}},
        )

    result = _provider(handler).enrich_contact(_contact(), _account())

    assert result.email is None
    assert result.email_confidence == "invalid"


def test_no_person_match_is_found_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    result = _provider(handler).enrich_contact(_contact(), _account())
    assert result.found is False


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_server_and_rate_limit_errors_are_retryable(status_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="try again later")

    with pytest.raises(RetryableError):
        _provider(handler).enrich_contact(_contact(), _account())


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_client_and_auth_errors_are_non_retryable(status_code):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="bad request")

    with pytest.raises(NonRetryableError):
        _provider(handler).enrich_contact(_contact(), _account())


def test_the_confirmed_real_403_payload_is_non_retryable():
    """The actual live response this project received when checking
    Apollo contact-enrichment access at the start of this step."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": "The api/v1/people/match API is not included in your Free "
                "plan and is not accessible, even with a master key.",
                "error_code": "API_INACCESSIBLE",
            },
        )

    with pytest.raises(NonRetryableError):
        _provider(handler).enrich_contact(_contact(), _account())


def test_missing_api_key_is_rejected_at_construction():
    with pytest.raises(ValueError):
        ApolloContactEnrichmentProvider(api_key="")
