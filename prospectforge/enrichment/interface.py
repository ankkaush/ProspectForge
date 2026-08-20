"""The EnrichmentProvider port (see ADR-005) - discovery and enrichment are
kept as separate interfaces even though Apollo happens to implement both
right now, because they're different capabilities: discovery searches for
new accounts, enrichment fills in fields for an account we already have.
If we ever swapped one without the other, a shared interface would make
that impossible to express.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from pydantic import BaseModel

from prospectforge.models import Account


class EnrichmentResult(BaseModel):
    """The output of one enrichment attempt. `found=False` is a normal,
    successful outcome (the provider simply has no data for this account) -
    not an error. See the roadmap's failure scenario for this step: "no
    data" must resolve to unknown, never be treated as a fit failure."""

    found: bool
    tech_stack: Optional[List[str]] = None
    funding_stage: Optional[str] = None
    growth_signal: Optional[str] = None
    raw_payload: dict = {}


class EnrichmentProvider(ABC):
    @abstractmethod
    def enrich_account(self, account: Account) -> EnrichmentResult:
        """Look up additional data for one account. Raise
        infra.retry.RetryableError for a transient failure (timeout, rate
        limit, 5xx) and infra.retry.NonRetryableError for one retrying
        won't fix (bad request, bad auth). A provider simply not having
        data for this account is NOT an error - return
        EnrichmentResult(found=False)."""
        raise NotImplementedError
