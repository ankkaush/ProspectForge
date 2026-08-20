"""QualificationResult - the synthesis of fit + evidence + data completeness
into an explainable verdict (Step 15). `evidence_ids` is what lets the
rationale-generation step be checked for hallucination: every claim in
`rationale_text` must trace back to an id in this list, or it's rejected
and a templated fallback is used instead (see roadmap step 15 failure
scenarios)."""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import Field

from .base import ForgeModel, new_id, utcnow
from .enums import QualificationStatus


class QualificationResult(ForgeModel):
    id: uuid.UUID = Field(default_factory=new_id)
    account_id: uuid.UUID
    contact_id: Optional[uuid.UUID] = None

    status: QualificationStatus
    reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)

    evidence_ids: list[uuid.UUID] = Field(default_factory=list)
    rationale_text: Optional[str] = None  # AI-drafted, human-readable summary;
    # every factual claim in it must cite one of evidence_ids

    evaluated_at: str = Field(default_factory=lambda: utcnow().isoformat())
