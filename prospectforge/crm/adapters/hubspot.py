"""HubSpotAdapter - the only file allowed to know HubSpot's CRM v3/v4
object shapes. Not yet verified against a live call (no HUBSPOT_API_KEY in
.env as of Step 18) - built against HubSpot's documented Companies/
Contacts/Notes/Associations API shapes, the same "documented, then
live-verified once a key exists" sequence Apollo's providers followed
(see docs/adr/003's addendum for why that verification step matters -
Apollo's own docs didn't match its free-tier access reality).

Idempotency: search-then-create for both objects, by domain (company) and
email (contact) - never trusts a locally-cached id. This is what makes a
second sync attempt after a partial failure (company created, contact
write failed) self-healing: the retry's company search finds the company
that already exists instead of creating a duplicate.

"Who wins" on a match (the roadmap's named failure scenario): HubSpot
does. A matched company/contact's existing properties are never
overwritten - a rep may have already edited that record by hand. A match
is used only to get the id for the association + note; only a genuinely
new object gets our property set.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import httpx

from infra.retry import NonRetryableError, RetryableError

from ..interface import CRMAdapter, CRMSyncInput, CRMSyncResult

HUBSPOT_BASE_URL = "https://api.hubapi.com"

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# HubSpot's defined numeric id for a "note to contact" association -
# a HubSpot-defined constant (HUBSPOT_DEFINED category), not something we
# choose. See HubSpot's Associations API reference for the full list.
_NOTE_TO_CONTACT_ASSOCIATION_TYPE_ID = 202


class HubSpotAdapter(CRMAdapter):
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = HUBSPOT_BASE_URL,
        client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not api_key:
            raise ValueError("HubSpotAdapter requires a non-empty api_key")
        self._api_key = api_key
        self._base_url = base_url
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def sync_prospect(self, input: CRMSyncInput) -> CRMSyncResult:
        company_id, company_matched = self._find_or_create_company(input)
        contact_id, contact_matched = self._find_or_create_contact(input)
        self._associate_company_and_contact(company_id, contact_id)
        self._attach_rationale_note(contact_id, input)

        return CRMSyncResult(
            crm_contact_id=contact_id,
            company_matched_existing=company_matched,
            contact_matched_existing=contact_matched,
        )

    # --- company -------------------------------------------------------

    def _find_or_create_company(self, input: CRMSyncInput) -> tuple[str, bool]:
        existing_id = self._search_one(
            "/crm/v3/objects/companies/search", "domain", input.account_domain
        )
        if existing_id is not None:
            return existing_id, True

        properties: dict[str, Any] = {"name": input.account_name, "domain": input.account_domain}
        if input.account_industry:
            properties["industry"] = input.account_industry
        created = self._request("POST", "/crm/v3/objects/companies", json={"properties": properties})
        return created["id"], False

    # --- contact ---------------------------------------------------------

    def _find_or_create_contact(self, input: CRMSyncInput) -> tuple[str, bool]:
        existing_id = self._search_one(
            "/crm/v3/objects/contacts/search", "email", input.contact_email
        )
        if existing_id is not None:
            return existing_id, True

        first_name, _, last_name = input.contact_name.partition(" ")
        properties: dict[str, Any] = {
            "email": input.contact_email,
            "firstname": first_name,
            "lifecyclestage": "salesqualifiedlead",
        }
        if last_name:
            properties["lastname"] = last_name
        if input.contact_title:
            properties["jobtitle"] = input.contact_title
        created = self._request("POST", "/crm/v3/objects/contacts", json={"properties": properties})
        return created["id"], False

    # --- association + note --------------------------------------------

    def _associate_company_and_contact(self, company_id: str, contact_id: str) -> None:
        self._request(
            "PUT",
            f"/crm/v4/objects/companies/{company_id}/associations/default/contacts/{contact_id}",
        )

    def _attach_rationale_note(self, contact_id: str, input: CRMSyncInput) -> None:
        body = (
            f"ProspectForge qualification confidence: {input.qualification_confidence:.0%}\n\n"
            f"{input.rationale_text or 'No rationale text available.'}"
        )
        self._request(
            "POST",
            "/crm/v3/objects/notes",
            json={
                "properties": {
                    "hs_note_body": body,
                    "hs_timestamp": str(int(time.time() * 1000)),
                },
                "associations": [
                    {
                        "to": {"id": contact_id},
                        "types": [
                            {
                                "associationCategory": "HUBSPOT_DEFINED",
                                "associationTypeId": _NOTE_TO_CONTACT_ASSOCIATION_TYPE_ID,
                            }
                        ],
                    }
                ],
            },
        )

    # --- shared plumbing -------------------------------------------------

    def _search_one(self, path: str, property_name: str, value: str) -> Optional[str]:
        payload = self._request(
            "POST",
            path,
            json={
                "filterGroups": [
                    {"filters": [{"propertyName": property_name, "operator": "EQ", "value": value}]}
                ]
            },
        )
        results = payload.get("results") or []
        return results[0]["id"] if results else None

    def _request(self, method: str, path: str, *, json: Optional[dict] = None) -> dict:
        try:
            response = self._client.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.TimeoutException as exc:
            raise RetryableError(f"HubSpot request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise RetryableError(f"HubSpot request failed (network error): {exc}") from exc

        self._raise_for_status(response)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise RetryableError(f"HubSpot returned a non-JSON response: {exc}") from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        detail = f"HubSpot returned HTTP {response.status_code}: {response.text}"
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise RetryableError(detail, retry_after_seconds=_parse_retry_after(response))
        raise NonRetryableError(detail)


def _parse_retry_after(response: httpx.Response) -> Optional[float]:
    """HubSpot (like most REST APIs) sends a Retry-After header on 429s
    with the number of seconds to wait - honoring it directly is more
    accurate than guessing via exponential backoff (Step 19's
    rate-limit-aware pacing)."""

    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None
