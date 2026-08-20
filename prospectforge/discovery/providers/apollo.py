"""ApolloDiscoveryProvider - the only file in the project allowed to know
Apollo's request/body field names, response shape, or HTTP quirks. See
ADR-003 (why Apollo) and ADR-005 (why this boundary exists).

Endpoint and field names are taken from Apollo's public API docs
(https://docs.apollo.io/reference/organization-search): POST
/api/v1/mixed_companies/search, authenticated via an `x-api-key` header.
I don't have a live Apollo API key to verify a real response against, so
the organization-field mapping below (industry, estimated_num_employees,
etc.) is my best-effort reading of Apollo's documented/commonly-observed
field names - if a live response uses different keys, `_map_organization`
is the one place that needs adjusting; nothing else in the project is
affected, which is exactly what the adapter boundary is for.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import httpx

from infra.retry import NonRetryableError, RetryableError
from prospectforge.models import Account

from ..interface import DiscoveredOrganization, DiscoveryCriteria, DiscoveryPage, DiscoveryProvider

APOLLO_BASE_URL = "https://api.apollo.io/api/v1"
SEARCH_PATH = "/mixed_companies/search"

# HTTP statuses worth retrying: rate limits and server-side failures. A
# request that's malformed or unauthorized will never succeed by retrying
# it unchanged, so those are NOT in this set - see NonRetryableError below.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ApolloDiscoveryProvider(DiscoveryProvider):
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = APOLLO_BASE_URL,
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not api_key:
            raise ValueError("ApolloDiscoveryProvider requires a non-empty api_key")
        self._api_key = api_key
        self._base_url = base_url
        # Accepting an injected httpx.Client (used by tests, via
        # httpx.MockTransport) is what lets the mapping/retry-classification
        # logic below be tested without any real network call.
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def search_accounts(
        self, criteria: DiscoveryCriteria, *, page: int, per_page: int
    ) -> DiscoveryPage:
        body: dict[str, Any] = {"page": page, "per_page": per_page}
        if criteria.industries:
            body["q_organization_keyword_tags"] = criteria.industries
        if criteria.geographies:
            body["organization_locations"] = criteria.geographies
        if criteria.employee_count_min is not None or criteria.employee_count_max is not None:
            lo = criteria.employee_count_min if criteria.employee_count_min is not None else 0
            hi = criteria.employee_count_max if criteria.employee_count_max is not None else 1_000_000
            body["organization_num_employees_ranges"] = [f"{lo},{hi}"]

        try:
            response = self._client.post(
                f"{self._base_url}{SEARCH_PATH}",
                json=body,
                headers={"x-api-key": self._api_key, "Content-Type": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise RetryableError(f"Apollo request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise RetryableError(f"Apollo request failed (network error): {exc}") from exc

        self._raise_for_status(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise RetryableError(f"Apollo returned a non-JSON response: {exc}") from exc

        return self._map_page(payload, page=page)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = f"Apollo returned HTTP {response.status_code}: {response.text[:500]}"
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise RetryableError(detail)
        raise NonRetryableError(detail)

    def _map_page(self, payload: dict, *, page: int) -> DiscoveryPage:
        raw_organizations = payload.get("organizations", [])
        pagination = payload.get("pagination", {})

        organizations = [self._map_organization(org) for org in raw_organizations]

        return DiscoveryPage(
            organizations=organizations,
            page=pagination.get("page", page),
            total_pages=pagination.get("total_pages", page),
            total_entries=pagination.get("total_entries", len(raw_organizations)),
        )

    def _map_organization(self, org: dict) -> DiscoveredOrganization:
        domain = org.get("primary_domain") or self._domain_from_website_url(org.get("website_url"))
        if not domain:
            return DiscoveredOrganization(
                account=None,
                raw_payload=org,
                skip_reason="no domain in Apollo response - cannot dedupe or identify this account",
            )

        name = org.get("name")
        if not name:
            return DiscoveredOrganization(
                account=None,
                raw_payload=org,
                skip_reason="no name in Apollo response",
            )

        account = Account(
            id=uuid.uuid4(),
            domain=domain,
            name=name,
            industry=org.get("industry"),
            employee_count=org.get("estimated_num_employees"),
            geography=self._geography_from_org(org),
        )
        return DiscoveredOrganization(account=account, raw_payload=org, skip_reason=None)

    @staticmethod
    def _domain_from_website_url(website_url: Optional[str]) -> Optional[str]:
        if not website_url:
            return None
        domain = website_url.replace("https://", "").replace("http://", "").rstrip("/")
        return domain.split("/")[0] or None

    @staticmethod
    def _geography_from_org(org: dict) -> Optional[str]:
        # Account.geography holds the country alone, not "city, state,
        # country" - the ICP's geography criterion does an exact match
        # against country names (see Step 6's seed config), so a compound
        # string would never match even a genuinely supported location.
        # City/state detail isn't lost - it's still in raw_payload
        # (ProviderRecord) for anything that needs it later.
        return org.get("country") or None
