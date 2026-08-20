"""ApolloEnrichmentProvider - the only file allowed to know Apollo's
organization-enrichment request/response shape. Endpoint and field names
verified with a live call against a real Apollo API key (2026-08-19), not
just documentation - see docs/adr/003 for the discovery-endpoint access
issue this project ran into; enrichment, unlike search, IS accessible on
the free plan.

GET /api/v1/organizations/enrich?domain=... - a 200 with no "organization"
key (or an empty body) means Apollo simply has no data for this domain,
which is a normal outcome (EnrichmentResult(found=False)), not a failure.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from infra.retry import NonRetryableError, RetryableError
from prospectforge.models import Account

from ..interface import EnrichmentProvider, EnrichmentResult

APOLLO_BASE_URL = "https://api.apollo.io/api/v1"
ENRICH_PATH = "/organizations/enrich"

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Thresholds for turning Apollo's 12-month headcount growth percentage into
# the categorical labels this project's ICP criteria check against (see
# Step 6's seed config). This is a first-pass heuristic I'm choosing now,
# not something Apollo defines - worth revisiting once Step 10 shows how
# it actually behaves against real enriched data.
_HIRING_GROWTH_THRESHOLD = 0.15
_SCALING_GROWTH_THRESHOLD = 0.05


class ApolloEnrichmentProvider(EnrichmentProvider):
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = APOLLO_BASE_URL,
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not api_key:
            raise ValueError("ApolloEnrichmentProvider requires a non-empty api_key")
        self._api_key = api_key
        self._base_url = base_url
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def enrich_account(self, account: Account) -> EnrichmentResult:
        try:
            response = self._client.get(
                f"{self._base_url}{ENRICH_PATH}",
                params={"domain": account.domain},
                headers={"x-api-key": self._api_key},
            )
        except httpx.TimeoutException as exc:
            raise RetryableError(f"Apollo enrichment request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise RetryableError(f"Apollo enrichment request failed (network error): {exc}") from exc

        self._raise_for_status(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise RetryableError(f"Apollo returned a non-JSON response: {exc}") from exc

        org = payload.get("organization")
        if not org:
            return EnrichmentResult(found=False, raw_payload=payload)

        return EnrichmentResult(
            found=True,
            tech_stack=org.get("technology_names") or None,
            funding_stage=org.get("latest_funding_stage") or None,
            growth_signal=self._growth_signal_from_org(org),
            raw_payload=org,
        )

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = f"Apollo returned HTTP {response.status_code}: {response.text[:500]}"
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise RetryableError(detail)
        raise NonRetryableError(detail)

    @staticmethod
    def _growth_signal_from_org(org: dict) -> Optional[str]:
        growth: Any = org.get("organization_headcount_twelve_month_growth")
        if growth is None:
            return None
        if growth >= _HIRING_GROWTH_THRESHOLD:
            return "hiring"
        if growth >= _SCALING_GROWTH_THRESHOLD:
            return "scaling"
        return None
