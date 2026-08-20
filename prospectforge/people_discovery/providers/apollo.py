"""ApolloPersonDiscoveryProvider - the only file allowed to know Apollo's
people-search request/response shape. Endpoint confirmed live
(2026-08-19): POST /api/v1/mixed_people/search returns a structured
API_INACCESSIBLE 403 on this project's Free plan (not a 404), confirming
the path itself is correct even though we can't currently call it for
real - see ADR-003's addendum and this step's notes.

Field names below (person_titles, q_organization_domains_list,
person_seniorities; response: total_entries, people[].first_name/
last_name_obfuscated/title) are from Apollo's public API docs, not a
verified live response. Note in particular: on lower Apollo tiers,
last_name_obfuscated may not be a real surname - the mapper below is a
best-effort reading, flagged the same way the discovery/enrichment
providers were, for adjustment once real access exists.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import httpx

from infra.retry import NonRetryableError, RetryableError
from prospectforge.models import Account, Contact

from ..interface import DiscoveredPerson, PersonDiscoveryPage, PersonDiscoveryProvider, PersonSearchCriteria

APOLLO_BASE_URL = "https://api.apollo.io/api/v1"
SEARCH_PATH = "/mixed_people/search"

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ApolloPersonDiscoveryProvider(PersonDiscoveryProvider):
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = APOLLO_BASE_URL,
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not api_key:
            raise ValueError("ApolloPersonDiscoveryProvider requires a non-empty api_key")
        self._api_key = api_key
        self._base_url = base_url
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def search_people(
        self,
        account: Account,
        criteria: PersonSearchCriteria,
        *,
        page: int,
        per_page: int,
    ) -> PersonDiscoveryPage:
        body: dict[str, Any] = {
            "q_organization_domains_list": [account.domain],
            "page": page,
            "per_page": per_page,
        }
        # Apollo's person_seniorities uses its own vocabulary
        # (owner/founder/c_suite/vp/director/...), not our free-text
        # seniority keywords - rather than guess a mapping, we let Apollo
        # return a broader set and apply our own persona matching
        # afterward, same as the CSV provider does. This keeps the
        # matching rule identical regardless of which provider is active.

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

        return self._map_page(payload, account.id, criteria, page=page)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = f"Apollo returned HTTP {response.status_code}: {response.text[:500]}"
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise RetryableError(detail)
        raise NonRetryableError(detail)

    def _map_page(
        self, payload: dict, account_id: uuid.UUID, criteria: PersonSearchCriteria, *, page: int
    ) -> PersonDiscoveryPage:
        from prospectforge.persona.matcher import match_title_against_keywords

        raw_people = payload.get("people", [])
        people = []
        for person in raw_people:
            title = person.get("title")
            matched_rule = match_title_against_keywords(
                title, criteria.seniority_keywords, criteria.department_keywords
            )
            if matched_rule is None:
                continue  # Apollo's own filters are broader than our persona - apply ours here too
            people.append(self._map_person(person, account_id, matched_rule))

        return PersonDiscoveryPage(
            people=people,
            page=page,
            total_pages=payload.get("total_pages", page),
            total_entries=payload.get("total_entries", len(raw_people)),
        )

    @staticmethod
    def _map_person(person: dict, account_id: uuid.UUID, matched_rule: str) -> DiscoveredPerson:
        first_name = person.get("first_name")
        last_name = person.get("last_name_obfuscated") or person.get("last_name")
        name = " ".join(p for p in [first_name, last_name] if p).strip()

        if not name:
            return DiscoveredPerson(
                contact=None,
                raw_payload=person,
                skip_reason="no usable name in Apollo response",
                matched_rule=matched_rule,
            )

        contact = Contact(
            id=uuid.uuid4(),
            account_id=account_id,
            name=name,
            title=person.get("title"),
        )
        return DiscoveredPerson(contact=contact, raw_payload=person, matched_rule=matched_rule)
