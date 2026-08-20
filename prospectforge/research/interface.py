"""The ResearchProvider port (see ADR-005). Reuses the Step 4 Evidence
model directly as the output shape - research's whole job is producing
Evidence, so there's no separate research-specific result type needed for
that part.

Design note (see docs/adr/007-research-provider-design.md for the full
reasoning): the roadmap originally sketched research as two separate
concerns - a ResearchSource (search/fetch) abstraction and a separate LLM
extractor. This implementation collapses search and extraction into one
Claude API call, using Claude's native web_search server tool, rather than
building and maintaining a second provider integration (a search API) just
to hand raw pages to a second LLM call. Retrieval still stays
deterministic in the sense the roadmap meant: what to search for (the
query built from account name/domain) is decided by our code, not left to
the model's free judgment about what to research at all.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List

from pydantic import BaseModel

from prospectforge.models import Account, Evidence


class ResearchResult(BaseModel):
    evidence: List[Evidence]
    # audit trail: which URLs were actually retrieved this call, and how
    # many claims were dropped for citing a source that wasn't - see
    # extractor.py for why this cross-check exists
    verified_urls: List[str] = []
    dropped_claim_count: int = 0
    raw_response: Any = None


class ResearchProvider(ABC):
    @abstractmethod
    def research_account(self, account: Account) -> ResearchResult:
        """Research one account, returning sourced, confidence-tagged
        Evidence. Raise infra.retry.RetryableError for a transient failure
        (timeout, rate limit, 5xx) and infra.retry.NonRetryableError for
        one retrying won't fix (bad request, bad auth). Finding nothing
        useful is NOT an error - return ResearchResult(evidence=[])."""
        raise NotImplementedError
