"""The PersonDiscoveryProvider port (see ADR-005) - deliberately reuses the
DiscoveryProvider pattern from Step 7 (paginated search -> mapped results
with raw payload retained) rather than inventing a new shape, per the
roadmap's explicit note that this step extends the proven boundary instead
of building a new one.

Distinct from DiscoveryProvider itself, though: person search is always
scoped to one account (search_people(account, ...), not an open-ended
criteria search) - Step 12 only looks for people at companies we've
already decided are worth pursuing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from pydantic import BaseModel

from prospectforge.models import Account, Contact


class PersonSearchCriteria(BaseModel):
    """Provider-independent search criteria, built from a PersonaConfig
    (see people_discovery/service.py) - never a provider's own field
    names."""

    seniority_keywords: List[str] = []
    department_keywords: List[str] = []


class DiscoveredPerson(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    contact: Optional[Contact]
    raw_payload: dict
    skip_reason: Optional[str] = None
    matched_rule: Optional[str] = None  # which persona keywords matched - None if unmatched


class PersonDiscoveryPage(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    people: List[DiscoveredPerson]
    page: int
    total_pages: int
    total_entries: int


class PersonDiscoveryProvider(ABC):
    @abstractmethod
    def search_people(
        self,
        account: Account,
        criteria: PersonSearchCriteria,
        *,
        page: int,
        per_page: int,
    ) -> PersonDiscoveryPage:
        """Find candidate contacts at `account` matching `criteria`. Raise
        infra.retry.RetryableError / NonRetryableError per the same
        convention as every other provider port."""
        raise NotImplementedError
