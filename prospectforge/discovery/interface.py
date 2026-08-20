"""The DiscoveryProvider port (see ADR-005) - the contract discovery/service.py
depends on, that ApolloDiscoveryProvider (and any future provider) implements.

Nothing in this file knows Apollo exists. That's the point: the pipeline
code that calls a DiscoveryProvider only ever sees these types, so a
provider swap later touches one adapter file, not this interface or the
service that uses it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from prospectforge.models import Account


class DiscoveryCriteria(BaseModel):
    """Provider-independent search criteria, built from an ICPConfig's
    pre-enrichment criteria (see discovery/criteria.py) - never Apollo's
    own field names."""

    industries: List[str] = Field(default_factory=list)
    employee_count_min: Optional[int] = None
    employee_count_max: Optional[int] = None
    geographies: List[str] = Field(default_factory=list)


class DiscoveredOrganization(BaseModel):
    """One search result. `account` is the best-effort mapping onto our
    Account contract; it's None when the provider's record couldn't be
    mapped (most commonly: no domain, which is our dedup key - an account
    we can't identify isn't one we can safely persist or dedupe later).
    `raw_payload` is kept regardless, for ProviderRecord persistence -
    nothing the provider returned is ever discarded, even a record we
    can't use yet."""

    model_config = {"arbitrary_types_allowed": True}

    account: Optional[Account]
    raw_payload: dict
    skip_reason: Optional[str] = None


class DiscoveryPage(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    organizations: List[DiscoveredOrganization]
    page: int
    total_pages: int
    total_entries: int


class DiscoveryProvider(ABC):
    @abstractmethod
    def search_accounts(
        self, criteria: DiscoveryCriteria, *, page: int, per_page: int
    ) -> DiscoveryPage:
        """Fetch one page of results matching `criteria`. Implementations
        should raise infra.retry.RetryableError for a transient failure
        (timeout, rate limit, 5xx) and infra.retry.NonRetryableError for a
        failure retrying won't fix (bad request, bad auth) - the caller
        wraps this in call_with_retry and relies on that distinction."""
        raise NotImplementedError
