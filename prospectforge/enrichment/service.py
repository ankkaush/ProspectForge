"""Orchestrates enrichment for a run: finds accounts waiting at
status=ADVANCED (first attempt) or ENRICHMENT_FAILED (retry - fixed at
Step 19's idempotency review, which found this query originally only
looked at ADVANCED, silently orphaning every failed account forever even
though ENRICHMENT_FAILED -> ENRICHED/FIT_EVALUATED was already a legal
transition in the state machine), enriches each independently, and
transitions each to ENRICHED or ENRICHMENT_FAILED.

Per-item failure isolation, not run-level: unlike discovery's single bulk
call (Step 7 - one failure fails the whole run, since there's no partial
page to save), enrichment calls the provider once per account. One
account's call exhausting its retries doesn't stop the rest of the batch -
it's marked ENRICHMENT_FAILED, logged, counted, and the loop moves on. This
is the technical audit's run-level vs. per-item failure distinction,
now actually exercised for the first time.

Same query-by-status resumability pattern as prefilter (Step 8): any
account sitting at ADVANCED is fair game, regardless of which run
discovered or prefiltered it.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging import log_context
from app.mappers import orm_to_account
from app.orm import AccountORM, ProviderRecordORM
from infra.retry import NonRetryableError, RetryableError, call_with_retry
from prospectforge.models.enums import AccountStatus

from .interface import EnrichmentProvider

logger = logging.getLogger("prospectforge.enrichment")


def get_default_enrichment_provider() -> EnrichmentProvider:
    """Factory for the provider used when the caller doesn't inject one -
    separate from run_enrichment so tests can monkeypatch just this, same
    pattern as discovery/service.py's get_default_discovery_provider.

    Unlike discovery, Apollo's enrichment endpoint IS accessible on this
    project's free plan (verified live - see this step's notes), so this
    defaults straight to ApolloEnrichmentProvider rather than needing a
    CSV-style stand-in."""

    from .providers.apollo import ApolloEnrichmentProvider

    api_key = get_settings().apollo_api_key
    if not api_key:
        raise RuntimeError(
            "APOLLO_API_KEY is not set - required to run enrichment against "
            "the real Apollo provider. Set it in .env, or pass an explicit "
            "enrichment_provider to run_enrichment() for testing."
        )
    return ApolloEnrichmentProvider(api_key=api_key)


def run_enrichment(
    run_id: uuid.UUID,
    session: Session,
    *,
    provider: Optional[EnrichmentProvider] = None,
) -> Dict[str, Any]:
    with log_context(run_id=str(run_id)):
        active_provider = provider or get_default_enrichment_provider()

        summary = {
            "evaluated": 0,
            "enriched": 0,
            "no_data_found": 0,
            "enrichment_failed": 0,
        }

        advanced_accounts = (
            session.query(AccountORM)
            .filter(AccountORM.status.in_([AccountStatus.ADVANCED, AccountStatus.ENRICHMENT_FAILED]))
            .all()
        )

        for account_orm in advanced_accounts:
            with log_context(account_id=str(account_orm.id)):
                summary["evaluated"] += 1
                account = orm_to_account(account_orm)

                try:
                    result = call_with_retry(
                        lambda: active_provider.enrich_account(account),
                        session=session,
                        run_id=run_id,
                        account_id=account_orm.id,
                        provider="apollo",
                        operation="account_enrichment",
                    )
                except (RetryableError, NonRetryableError) as exc:
                    account.transition_to(AccountStatus.ENRICHMENT_FAILED)
                    account_orm.status = account.status
                    summary["enrichment_failed"] += 1
                    logger.info(
                        "enrichment failed for domain=%s: %s", account_orm.domain, exc
                    )
                    session.flush()
                    continue

                session.add(
                    ProviderRecordORM(
                        account_id=account_orm.id,
                        provider="apollo",
                        operation="account_enrichment",
                        payload=result.raw_payload,
                    )
                )

                if result.found:
                    account_orm.tech_stack = result.tech_stack
                    account_orm.funding_stage = result.funding_stage
                    account_orm.growth_signal = result.growth_signal
                    summary["enriched"] += 1
                else:
                    summary["no_data_found"] += 1

                account.transition_to(AccountStatus.ENRICHED)
                account_orm.status = account.status

                logger.info(
                    "enriched account domain=%s found=%s tech_stack=%s funding_stage=%s growth_signal=%s",
                    account_orm.domain,
                    result.found,
                    result.tech_stack,
                    result.funding_stage,
                    result.growth_signal,
                )
                session.flush()

        logger.info("enrichment completed with summary=%s", summary)
        return summary
