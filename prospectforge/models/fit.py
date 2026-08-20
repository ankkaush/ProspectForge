"""FitResult - the outcome of evaluating an Account against the current
ICPConfig, at one of the two passes (prefilter or full - see roadmap steps
8 and 10). Kept as its own append-only record, rather than overwriting a
single field on Account, so both passes' reasoning stays inspectable."""

from __future__ import annotations

import uuid

from pydantic import Field

from .base import ForgeModel, new_id, utcnow
from .enums import FitPassType, FitTier


class FitResult(ForgeModel):
    id: uuid.UUID = Field(default_factory=new_id)
    account_id: uuid.UUID

    pass_type: FitPassType
    tier: FitTier
    reasons: list[str] = Field(default_factory=list)  # one entry per
    # criterion that drove the tier - this is what answers "why is this
    # account Tier 2 and not Tier 1" concretely, per the roadmap's exit
    # criteria for step 10

    evaluated_at: str = Field(default_factory=lambda: utcnow().isoformat())
