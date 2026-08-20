"""ProspectRecord - the CRM-bound composite: the finished, human-reviewed
package that Step 18 (CRM sync) actually writes to HubSpot. References the
underlying records by id rather than re-embedding them, so this stays a
thin "here's what's ready to sync" view instead of a second copy of the
data."""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import Field

from .base import ForgeModel, new_id, utcnow
from .enums import ReviewDecision


class ProspectRecord(ForgeModel):
    id: uuid.UUID = Field(default_factory=new_id)

    account_id: uuid.UUID
    contact_id: uuid.UUID
    qualification_result_id: uuid.UUID

    priority_score: Optional[float] = None
    priority_rank: Optional[int] = None

    review_decision: ReviewDecision = ReviewDecision.PENDING
    review_reason: Optional[str] = None
    reviewed_at: Optional[str] = None

    crm_object_id: Optional[str] = None  # set once synced
    synced_at: Optional[str] = None

    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
