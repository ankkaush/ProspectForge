"""The CRMAdapter port (see ADR-005) - the pipeline core (and
sync_service.py) only ever talks to this interface, never to
crm/adapters/hubspot.py's HubSpot-specific request/response shapes
directly. Swapping CRMs later (Salesforce, Dynamics) means writing one new
adapter file; nothing here or in sync_service.py should need to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel


class CRMSyncInput(BaseModel):
    """Everything one adapter call needs to sync a single account+contact
    pair - already-flattened, already-decided data. The adapter maps this
    onto its CRM's own object shape; nothing upstream of this needs to
    know what that shape is."""

    account_name: str
    account_domain: str
    account_industry: Optional[str] = None

    contact_name: str
    contact_email: str
    contact_title: Optional[str] = None

    qualification_confidence: float
    rationale_text: Optional[str] = None


class CRMSyncResult(BaseModel):
    """crm_contact_id is what gets persisted onto ProspectRecord.crm_object_id
    - the contact, not the company, since a ProspectRecord's identity is
    the (account, contact) pair, and a company can be shared by several
    ProspectRecords (see prospectforge/crm/sync_service.py)."""

    crm_contact_id: str
    company_matched_existing: bool
    contact_matched_existing: bool


class CRMAdapter(ABC):
    @abstractmethod
    def sync_prospect(self, input: CRMSyncInput) -> CRMSyncResult:
        """Idempotent upsert: finds-or-creates the company (matched by
        domain), finds-or-creates the contact (matched by email),
        associates them, and attaches a note with the qualification
        rationale. Raise infra.retry.RetryableError / NonRetryableError
        per the standard convention - this method itself never decides to
        skip or degrade, that's sync_service.py's job."""
        raise NotImplementedError
