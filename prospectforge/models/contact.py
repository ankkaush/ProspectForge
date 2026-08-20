"""The Contact contract - a specific, verified-or-verifying person, always
linked to an Account. See docs/adr and Step 1 notes for why Contact and
Account are modeled as separate objects rather than one merged "lead" type."""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import Field

from .base import ForgeModel, new_id, utcnow
from .enums import ContactStatus


class Contact(ForgeModel):
    id: uuid.UUID = Field(default_factory=new_id)
    account_id: uuid.UUID

    name: str
    title: Optional[str] = None
    seniority: Optional[str] = None
    department: Optional[str] = None

    email: Optional[str] = None
    # "unknown" until contact enrichment runs; never silently promoted to
    # "verified" just because a value is present - see step 13's failure
    # scenario in the roadmap
    email_confidence: Optional[str] = None  # e.g. "verified" | "unverified" | None
    linkedin_url: Optional[str] = None

    status: ContactStatus = ContactStatus.DISCOVERED

    # Step 20 (GDPR right-to-erasure) - set once erasure.py has scrubbed
    # this contact's personal-identifying fields. None = never erased.
    erased_at: Optional[str] = None

    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: utcnow().isoformat())
