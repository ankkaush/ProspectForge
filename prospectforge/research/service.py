"""Orchestrates research for a run: finds accounts waiting at
status=FIT_EVALUATED (first attempt) or RESEARCH_FAILED (retry - fixed at
Step 19's idempotency review; this query originally only looked at
FIT_EVALUATED, silently orphaning every failed account forever even
though RESEARCH_FAILED -> RESEARCHED was already a legal transition) and,
per account, either researches it (Tier 1, Tier 2, and Insufficient Data -
see the note below on why the roadmap's literal "Tier 1/2 only" is
extended by one case) or rejects it outright without spending an API call
(Tier 3, already-Rejected).

Why Insufficient Data is researched rather than skipped: Steps 8 and 10
both established the principle that missing data is never treated as a
rejection - an account with an unknown field still gets the benefit of the
doubt and advances. Excluding Insufficient Data accounts from research
here would contradict that principle at the last stage where it matters
most (this is the step that decides whether an account is pursued further
at all, via the REJECTED transition below). Tier 3 and already-Rejected
accounts, by contrast, have a definite negative signal - that's what makes
skipping them a legitimate cost-gating decision rather than a data-gap
punishment.

Per-item failure isolation, same as enrichment (Step 9): one account's
research call failing doesn't stop the batch - it's marked
RESEARCH_FAILED and the loop continues.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging import log_context
from app.mappers import orm_to_account
from app.orm import AccountORM, EvidenceORM
from infra.retry import NonRetryableError, RetryableError, call_with_retry
from prospectforge.models.enums import AccountStatus, FitTier

from .interface import ResearchProvider

logger = logging.getLogger("prospectforge.research")

# Tiers worth spending a research call on - see module docstring.
_RESEARCHABLE_TIERS = {FitTier.TIER_1, FitTier.TIER_2, FitTier.INSUFFICIENT_DATA}


def get_default_research_provider() -> ResearchProvider:
    """Factory for the provider used when the caller doesn't inject one -
    same pattern as discovery/enrichment's default-provider factories."""

    from .providers.anthropic_web_search import AnthropicWebSearchResearchProvider

    api_key = get_settings().anthropic_api_key
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set - required to run research against "
            "the real Claude web-search provider. Set it in .env, or pass an "
            "explicit research_provider to run_research() for testing."
        )
    return AnthropicWebSearchResearchProvider(api_key=api_key)


def run_research(
    run_id: uuid.UUID,
    session: Session,
    *,
    provider: Optional[ResearchProvider] = None,
) -> Dict[str, Any]:
    with log_context(run_id=str(run_id)):
        active_provider = provider or get_default_research_provider()

        summary = {
            "evaluated": 0,
            "researched": 0,
            "evidence_collected": 0,
            "claims_dropped": 0,
            "research_failed": 0,
            "not_pursued": 0,
        }

        fit_evaluated_accounts = (
            session.query(AccountORM)
            .filter(AccountORM.status.in_([AccountStatus.FIT_EVALUATED, AccountStatus.RESEARCH_FAILED]))
            .all()
        )

        for account_orm in fit_evaluated_accounts:
            with log_context(account_id=str(account_orm.id)):
                summary["evaluated"] += 1

                if account_orm.fit_tier not in _RESEARCHABLE_TIERS:
                    account = orm_to_account(account_orm)
                    account.transition_to(AccountStatus.REJECTED)
                    account_orm.status = account.status
                    summary["not_pursued"] += 1
                    logger.info(
                        "not pursued (tier=%s), skipping research: domain=%s",
                        account_orm.fit_tier.value if account_orm.fit_tier else None,
                        account_orm.domain,
                    )
                    session.flush()
                    continue

                account = orm_to_account(account_orm)

                try:
                    result = call_with_retry(
                        lambda: active_provider.research_account(account),
                        session=session,
                        run_id=run_id,
                        account_id=account_orm.id,
                        provider="anthropic",
                        operation="company_research",
                    )
                except (RetryableError, NonRetryableError) as exc:
                    account.transition_to(AccountStatus.RESEARCH_FAILED)
                    account_orm.status = account.status
                    summary["research_failed"] += 1
                    logger.info("research failed for domain=%s: %s", account_orm.domain, exc)
                    session.flush()
                    continue

                for evidence in result.evidence:
                    session.add(
                        EvidenceORM(
                            account_id=account_orm.id,
                            claim=evidence.claim,
                            source_type=evidence.source_type,
                            source_url=evidence.source_url,
                            confidence=evidence.confidence,
                        )
                    )

                account.transition_to(AccountStatus.RESEARCHED)
                account_orm.status = account.status

                summary["researched"] += 1
                summary["evidence_collected"] += len(result.evidence)
                summary["claims_dropped"] += result.dropped_claim_count

                logger.info(
                    "researched domain=%s -> %d evidence item(s), %d claim(s) dropped",
                    account_orm.domain,
                    len(result.evidence),
                    result.dropped_claim_count,
                )
                session.flush()

        logger.info("research completed with summary=%s", summary)
        return summary
