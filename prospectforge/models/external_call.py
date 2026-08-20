"""ExternalCallAttempt - one row per attempt at a retryable external call
(Apollo, an LLM, HubSpot). Added per the technical audit, alongside Run: this
is the record that makes a failure visible and debuggable - which provider,
which operation, which attempt number, what error - instead of a failure
only ever existing as a line in a log stream that scrolls away.

Step 5's shared retry utility writes one of these per attempt; steps 19-21
read them to verify retry/backoff behavior and to drive the dead-letter /
failed-state reporting.
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import Field

from .base import ForgeModel, new_id, utcnow
from .enums import CallStatus


class ExternalCallAttempt(ForgeModel):
    id: uuid.UUID = Field(default_factory=new_id)
    run_id: uuid.UUID
    account_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None

    provider: str  # plain string, e.g. "apollo" | "hubspot" | "claude" -
    # deliberately not an enum, for the same provider-independence reason
    # as ProviderRecord.provider
    operation: str  # e.g. "discovery" | "account_enrichment" | "crm_upsert"

    attempt_number: int = Field(ge=1)
    status: CallStatus
    error_message: Optional[str] = None

    requested_at: str = Field(default_factory=lambda: utcnow().isoformat())
    responded_at: Optional[str] = None
