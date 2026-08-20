"""Tests for HubSpotAdapter - the one file allowed to know HubSpot's CRM
v3/v4 request/response shapes. No live HubSpot access as of Step 18 (no
HUBSPOT_API_KEY in .env yet) - these are built against HubSpot's
documented API shapes, mocked via httpx.MockTransport, same pattern as
test_enrichment_apollo_provider.py.
"""

from __future__ import annotations

import httpx
import pytest

from infra.retry import NonRetryableError, RetryableError
from prospectforge.crm.adapters.hubspot import HubSpotAdapter
from prospectforge.crm.interface import CRMSyncInput


def _adapter(handler) -> HubSpotAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HubSpotAdapter(api_key="test-hubspot-token", client=client)


def _input(**overrides) -> CRMSyncInput:
    defaults = dict(
        account_name="Northstar Metrics",
        account_domain="northstar-metrics.com",
        account_industry="Software",
        contact_name="Jane Doe",
        contact_email="jane.doe@northstar-metrics.com",
        contact_title="VP of Revenue Operations",
        qualification_confidence=0.8,
        rationale_text="Tier 1 fit with a verified contact.",
    )
    defaults.update(overrides)
    return CRMSyncInput(**defaults)


def _empty_search(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"total": 0, "results": []})


# --- create path: no existing records ---------------------------------

def test_creates_company_and_contact_when_neither_exists():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer test-hubspot-token"

        if request.url.path == "/crm/v3/objects/companies/search":
            return _empty_search(request)
        if request.url.path == "/crm/v3/objects/companies":
            body = request.read()
            import json

            props = json.loads(body)["properties"]
            assert props["domain"] == "northstar-metrics.com"
            return httpx.Response(201, json={"id": "company-1", "properties": props})
        if request.url.path == "/crm/v3/objects/contacts/search":
            return _empty_search(request)
        if request.url.path == "/crm/v3/objects/contacts":
            import json

            props = json.loads(request.read())["properties"]
            assert props["email"] == "jane.doe@northstar-metrics.com"
            assert props["firstname"] == "Jane"
            assert props["lastname"] == "Doe"
            assert props["lifecyclestage"] == "salesqualifiedlead"
            return httpx.Response(201, json={"id": "contact-1", "properties": props})
        if request.url.path == "/crm/v4/objects/companies/company-1/associations/default/contacts/contact-1":
            return httpx.Response(200, json={})
        if request.url.path == "/crm/v3/objects/notes":
            return httpx.Response(201, json={"id": "note-1"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    adapter = _adapter(handler)
    result = adapter.sync_prospect(_input())

    assert result.crm_contact_id == "contact-1"
    assert result.company_matched_existing is False
    assert result.contact_matched_existing is False
    assert ("POST", "/crm/v3/objects/companies") in calls
    assert ("POST", "/crm/v3/objects/contacts") in calls
    assert ("POST", "/crm/v3/objects/notes") in calls


# --- idempotency: running twice doesn't duplicate ----------------------

def test_running_sync_twice_does_not_create_a_second_company_or_contact():
    """The core idempotency guarantee: a second sync call for the same
    input must find, not recreate."""

    create_calls = {"companies": 0, "contacts": 0}
    company_exists = {"value": False}
    contact_exists = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/crm/v3/objects/companies/search":
            if company_exists["value"]:
                return httpx.Response(200, json={"total": 1, "results": [{"id": "company-1", "properties": {}}]})
            return _empty_search(request)
        if path == "/crm/v3/objects/companies":
            create_calls["companies"] += 1
            company_exists["value"] = True
            return httpx.Response(201, json={"id": "company-1"})
        if path == "/crm/v3/objects/contacts/search":
            if contact_exists["value"]:
                return httpx.Response(200, json={"total": 1, "results": [{"id": "contact-1", "properties": {}}]})
            return _empty_search(request)
        if path == "/crm/v3/objects/contacts":
            create_calls["contacts"] += 1
            contact_exists["value"] = True
            return httpx.Response(201, json={"id": "contact-1"})
        if path.startswith("/crm/v4/objects/companies/"):
            return httpx.Response(200, json={})
        if path == "/crm/v3/objects/notes":
            return httpx.Response(201, json={"id": "note-1"})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    adapter = _adapter(handler)

    first = adapter.sync_prospect(_input())
    second = adapter.sync_prospect(_input())

    assert create_calls == {"companies": 1, "contacts": 1}
    assert first.crm_contact_id == second.crm_contact_id == "contact-1"
    assert second.company_matched_existing is True
    assert second.contact_matched_existing is True


# --- matched (near-duplicate) existing data: HubSpot's data wins --------

def test_matched_company_and_contact_are_not_overwritten():
    """A company/contact matched by domain/email keeps its existing
    HubSpot properties untouched - only used for the id."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/crm/v3/objects/companies/search":
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "results": [
                        {"id": "existing-company", "properties": {"name": "Northstar Metrics Inc. (Renamed by rep)"}}
                    ],
                },
            )
        if path == "/crm/v3/objects/contacts/search":
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "results": [{"id": "existing-contact", "properties": {"jobtitle": "Chief Revenue Officer"}}],
                },
            )
        if path == "/crm/v3/objects/companies" or path == "/crm/v3/objects/contacts":
            raise AssertionError("must not create a new object when one already matched")
        if path.startswith("/crm/v4/objects/companies/"):
            assert path == "/crm/v4/objects/companies/existing-company/associations/default/contacts/existing-contact"
            return httpx.Response(200, json={})
        if path == "/crm/v3/objects/notes":
            return httpx.Response(201, json={"id": "note-1"})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    adapter = _adapter(handler)
    result = adapter.sync_prospect(_input())

    assert result.crm_contact_id == "existing-contact"
    assert result.company_matched_existing is True
    assert result.contact_matched_existing is True


# --- partial failure: company created, contact write fails -------------

def test_company_created_then_contact_write_fails_raises_and_leaves_recoverable_state():
    """The named failure scenario: company created OK, contact call fails.
    A NonRetryableError must propagate (sync_service.py is responsible for
    not marking this record synced), and a subsequent retry's company
    search must find the already-created company rather than duplicating
    it - proven by test_running_sync_twice... above; this test proves the
    failure itself surfaces correctly."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/crm/v3/objects/companies/search":
            return _empty_search(request)
        if path == "/crm/v3/objects/companies":
            return httpx.Response(201, json={"id": "company-1"})
        if path == "/crm/v3/objects/contacts/search":
            return _empty_search(request)
        if path == "/crm/v3/objects/contacts":
            return httpx.Response(400, json={"message": "Property values were not valid"})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    adapter = _adapter(handler)

    with pytest.raises(NonRetryableError):
        adapter.sync_prospect(_input())


# --- HTTP error classification -----------------------------------------

def test_rate_limit_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "rate limited"})

    adapter = _adapter(handler)
    with pytest.raises(RetryableError):
        adapter.sync_prospect(_input())


def test_unauthorized_is_non_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid token"})

    adapter = _adapter(handler)
    with pytest.raises(NonRetryableError):
        adapter.sync_prospect(_input())


def test_timeout_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    adapter = _adapter(handler)
    with pytest.raises(RetryableError):
        adapter.sync_prospect(_input())


# --- Step 19: rate-limit-aware pacing -----------------------------------

def test_rate_limit_with_retry_after_header_carries_it_on_the_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"message": "rate limited"})

    adapter = _adapter(handler)
    with pytest.raises(RetryableError) as exc_info:
        adapter.sync_prospect(_input())

    assert exc_info.value.retry_after_seconds == 30.0


def test_rate_limit_without_retry_after_header_leaves_it_unset():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "rate limited"})

    adapter = _adapter(handler)
    with pytest.raises(RetryableError) as exc_info:
        adapter.sync_prospect(_input())

    assert exc_info.value.retry_after_seconds is None
