"""The RationaleProvider port (see ADR-005) - reuses the Anthropic-API
call pattern established in Step 11's ResearchProvider, but without the
web_search tool: rationale generation only phrases data already gathered
by the deterministic engine, it never needs to look anything up.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


class EvidenceSummary(BaseModel):
    """The minimal, already-vetted shape of an evidence item handed to
    the rationale model - just enough to phrase, not raw provider data."""

    id: UUID
    claim: str
    confidence: str


class RationaleContext(BaseModel):
    account_name: str
    fit_tier: str
    deterministic_reasons: List[str]
    evidence: List[EvidenceSummary]
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    contact_email_confidence: Optional[str] = None


class RationaleResult(BaseModel):
    rationale_text: Optional[str] = None  # None if the model's output was unusable
    dropped_statement_count: int = 0
    raw_response: Optional[dict] = None


class RationaleProvider(ABC):
    @abstractmethod
    def generate_rationale(self, context: RationaleContext) -> RationaleResult:
        """Raise infra.retry.RetryableError / NonRetryableError per the
        standard convention. A None rationale_text (model output couldn't
        be parsed/validated) is NOT an error - it signals the caller to
        fall back to a templated rationale, same as Step 11's
        found=False convention."""
        raise NotImplementedError
