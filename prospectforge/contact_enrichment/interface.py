"""The ContactEnrichmentProvider port (see ADR-005) - symmetric to Step 9's
EnrichmentProvider, at the contact level. Kept as a separate interface
(not a shared "EnrichmentProvider" reused for both accounts and contacts)
because the two enrich fundamentally different things with different
fields and different providers could plausibly diverge - same reasoning
ADR-005 already gives for discovery vs. enrichment being separate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel

from prospectforge.models import Account, Contact


class ContactEnrichmentResult(BaseModel):
    """found=False is a normal, successful outcome (the provider has no
    data for this person) - not an error, same convention as Step 9's
    EnrichmentResult. email_confidence is never upgraded past what the
    provider actually reports - see contact_enrichment/validators.py for
    why a malformed email never gets treated as a usable fact."""

    found: bool
    email: Optional[str] = None
    email_confidence: Optional[str] = None  # "verified" | "unverified" | "invalid" | None
    seniority: Optional[str] = None
    linkedin_url: Optional[str] = None
    raw_payload: dict = {}


class ContactEnrichmentProvider(ABC):
    @abstractmethod
    def enrich_contact(self, contact: Contact, account: Account) -> ContactEnrichmentResult:
        """`account` is needed alongside `contact` because matching a
        person in an external database reliably requires company context
        (name/domain), not just a name. Raise infra.retry.RetryableError /
        NonRetryableError per the standard convention."""
        raise NotImplementedError
