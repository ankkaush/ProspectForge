"""ApolloContactEnrichmentProvider - the only file allowed to know Apollo's
people/match request/response shape. Endpoint confirmed live (2026-08-19):
POST /api/v1/people/match returns a structured 403 API_INACCESSIBLE on
this project's Free plan (not a 404), confirming the path itself is
correct even though we can't currently call it for real.

Field names below (first_name/last_name/organization_name/domain in the
request; a top-level "person" object in the response with email/
email_status/seniority/linkedin_url) follow Apollo's public docs, not a
verified live response - same "best-effort, verify and adjust once real
access exists" caveat as the other Apollo adapters.

reveal_personal_emails is deliberately left False: we want the person's
work email (usually returned without it), not their personal email - see
docs/adr/009-contact-pii-handling.md on data minimization.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from infra.retry import NonRetryableError, RetryableError
from prospectforge.models import Account, Contact

from ..interface import ContactEnrichmentProvider, ContactEnrichmentResult
from ..validators import is_plausible_email

APOLLO_BASE_URL = "https://api.apollo.io/api/v1"
MATCH_PATH = "/people/match"

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ApolloContactEnrichmentProvider(ContactEnrichmentProvider):
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = APOLLO_BASE_URL,
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not api_key:
            raise ValueError("ApolloContactEnrichmentProvider requires a non-empty api_key")
        self._api_key = api_key
        self._base_url = base_url
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def enrich_contact(self, contact: Contact, account: Account) -> ContactEnrichmentResult:
        first_name, _, last_name = (contact.name or "").partition(" ")
        body: dict[str, Any] = {
            "first_name": first_name or None,
            "last_name": last_name or None,
            "organization_name": account.name,
            "domain": account.domain,
            "reveal_personal_emails": False,
        }

        try:
            response = self._client.post(
                f"{self._base_url}{MATCH_PATH}",
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

        return self._map_person(payload)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = f"Apollo returned HTTP {response.status_code}: {response.text[:500]}"
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise RetryableError(detail)
        raise NonRetryableError(detail)

    @staticmethod
    def _map_person(payload: dict) -> ContactEnrichmentResult:
        person = payload.get("person")
        if not person:
            return ContactEnrichmentResult(found=False, raw_payload=payload)

        raw_email = person.get("email")
        email_status = person.get("email_status")  # e.g. "verified" | "unverified" | "guessed"

        if raw_email and not is_plausible_email(raw_email):
            # Never trust a malformed email as a usable fact, regardless
            # of what status the provider claims for it.
            return ContactEnrichmentResult(
                found=True,
                email=None,
                email_confidence="invalid",
                seniority=person.get("seniority"),
                linkedin_url=person.get("linkedin_url"),
                raw_payload=person,
            )

        return ContactEnrichmentResult(
            found=True,
            email=raw_email or None,
            email_confidence=email_status,
            seniority=person.get("seniority"),
            linkedin_url=person.get("linkedin_url"),
            raw_payload=person,
        )
