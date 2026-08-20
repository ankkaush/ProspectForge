"""ProviderRecord - the raw response from a discovery/enrichment provider
call, kept verbatim for audit and replay.

Distinct from Evidence: Evidence is an unstructured, AI-extracted *claim*
with a confidence level. ProviderRecord is a structured, deterministic API
response - we don't doubt what Apollo returned, we just want a record of it
so we can debug a bad mapping later without re-calling the provider (and
re-spending API quota) to see what it originally said.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from pydantic import Field

from .base import ForgeModel, new_id, utcnow


class ProviderRecord(ForgeModel):
    id: uuid.UUID = Field(default_factory=new_id)
    account_id: Optional[uuid.UUID] = None
    contact_id: Optional[uuid.UUID] = None

    provider: str  # e.g. "apollo" - a plain string, not an enum, so adding a
    # new provider later never requires editing this core model
    operation: str  # e.g. "discovery" | "account_enrichment" | "contact_enrichment"

    payload: dict[str, Any]  # the provider's response, verbatim

    fetched_at: str = Field(default_factory=lambda: utcnow().isoformat())
