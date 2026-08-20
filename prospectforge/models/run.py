"""Run - one row per pipeline execution. Added to the data contracts as a
direct result of the pre-Step-4 technical audit: without a Run to anchor
every stage's "which accounts belong to this execution" queries against,
there's no way to answer "what already succeeded and where do we resume"
after a crash. See docs/architecture.md and docs/adr/006-run-and-state-model.md.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from pydantic import Field

from .base import ForgeModel, new_id, utcnow
from .enums import RunStatus


class Run(ForgeModel):
    id: uuid.UUID = Field(default_factory=new_id)
    icp_config_id: str

    status: RunStatus = RunStatus.PENDING

    started_at: str = Field(default_factory=lambda: utcnow().isoformat())
    completed_at: Optional[str] = None

    # coarse counts per stage, filled in as the run progresses - e.g.
    # {"discovered": 200, "advanced": 150, "enriched": 100,
    #  "enrichment_failed": 7, "qualified": 34}. This is what a human reads
    # first when checking "did the last run work, and if not, where did it
    # fail" (roadmap step 22's stated deliverable).
    summary: dict[str, Any] = Field(default_factory=dict)
