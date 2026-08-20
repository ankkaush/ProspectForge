"""Evidence - a single sourced, confidence-tagged claim about an Account.

This is the first-class provenance object referenced throughout the roadmap:
pydantic validation only guarantees a claim is well-shaped (has a source_url,
has a confidence value), never that it's true. Evidence exists so every
downstream consumer (qualification rationale, human review) can trace a
claim back to where it came from and how sure we are, instead of trusting a
free-floating sentence of AI output.
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import Field

from .base import ForgeModel, new_id, utcnow
from .enums import ConfidenceLevel, EvidenceSourceType


class Evidence(ForgeModel):
    id: uuid.UUID = Field(default_factory=new_id)
    account_id: uuid.UUID
    contact_id: Optional[uuid.UUID] = None

    claim: str  # e.g. "Posted 4 open engineering roles in the last 30 days"
    source_type: EvidenceSourceType
    source_url: Optional[str] = None  # required in practice for AI_INFERRED claims,
    # enforced by the extractor (step 11), not by the schema itself - a
    # manual note may legitimately have no URL

    confidence: ConfidenceLevel

    extracted_at: str = Field(default_factory=lambda: utcnow().isoformat())
