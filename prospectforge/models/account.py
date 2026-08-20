"""The Account contract - a company, persistent across runs.

Deliberately holds only structured, current-best-known fields. Raw provider
responses live separately in ProviderRecord (for audit/replay); unstructured
research claims live separately in Evidence (for provenance/confidence).
Account itself is the clean, queryable snapshot everything else is derived
from - this keeps it simple to reason about, per the project's
no-unnecessary-complexity rule.
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import Field

from .base import ForgeModel, new_id, utcnow
from .enums import ACCOUNT_STATUS_TRANSITIONS, AccountStatus, FitTier


class IllegalStatusTransition(ValueError):
    """Raised when code tries to move an Account to a status that isn't a
    legal next-state from its current one - e.g. RAW straight to SYNCED,
    which would skip every gate (fit, enrichment, qualification, review)
    the pipeline exists to enforce."""


class Account(ForgeModel):
    id: uuid.UUID = Field(default_factory=new_id)

    # identity / dedup key - see the audit's ingestion-time dedup checkpoint
    domain: str

    # structural fields - firmographic, pre-enrichment where noted
    name: str
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    geography: Optional[str] = None

    # post-enrichment fields - unknown until step 9 runs, so must be
    # representable as None ("unknown"), never a guessed default
    tech_stack: Optional[list[str]] = None
    funding_stage: Optional[str] = None
    growth_signal: Optional[str] = None

    # pipeline state
    status: AccountStatus = AccountStatus.RAW
    fit_tier: Optional[FitTier] = None

    # which run first discovered this account (accounts persist across runs,
    # so this is provenance, not an ownership link)
    discovered_in_run_id: Optional[uuid.UUID] = None

    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: utcnow().isoformat())

    def can_transition_to(self, new_status: AccountStatus) -> bool:
        return new_status in ACCOUNT_STATUS_TRANSITIONS.get(self.status, set())

    def transition_to(self, new_status: AccountStatus) -> None:
        """Move this account to a new status, or raise if the transition
        isn't a legal next-state. This is the enforcement point for the
        state machine defined in enums.ACCOUNT_STATUS_TRANSITIONS."""

        if not self.can_transition_to(new_status):
            raise IllegalStatusTransition(
                f"Account {self.id} cannot move from {self.status} to {new_status}"
            )
        self.status = new_status
        self.updated_at = utcnow().isoformat()
