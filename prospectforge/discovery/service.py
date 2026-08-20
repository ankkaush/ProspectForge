"""Orchestrates account discovery for a run: loads the ICP, builds
provider-independent criteria, pages through the DiscoveryProvider,
persists each result immediately, and applies the ingestion-time dedup
checkpoint (see the technical audit's "dedup as a 3-checkpoint capability").

Per-item persistence, not batch-then-save: each account is written to the
database as soon as it's mapped, before moving to the next one. This is
the mechanism (not a special feature) that makes a mid-run crash lose at
most the one in-flight item, never the accounts already processed - see
docs/architecture.md and ADR-006.

A failure fetching a page (after infra.retry's backoff is exhausted) is a
run-level failure, not a per-account one - there's no partial page to save,
since the page itself never arrived. This matches the technical audit's
distinction between run-level and per-item failures.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging import log_context
from app.orm import AccountORM, ProviderRecordORM
from infra.retry import call_with_retry
from prospectforge.icp.loader import load_icp_config

from .criteria import criteria_from_icp
from .interface import DiscoveryProvider

logger = logging.getLogger("prospectforge.discovery")

PER_PAGE = 100


def get_default_discovery_provider() -> DiscoveryProvider:
    """Factory for the provider used when the caller doesn't inject one.
    A separate function (not inlined into run_discovery) so tests can
    monkeypatch just this - swapping in a fake provider without touching
    run_discovery's actual orchestration logic at all.

    Which real provider this returns is a config setting
    (settings.discovery_provider), not a code change - see ADR-005 and
    ADR-003's addendum. Currently defaults to "csv" because Apollo's free
    plan has no search API access on this account; "apollo" remains fully
    built and tested (prospectforge/discovery/providers/apollo.py) for
    when real API access exists.
    """

    provider_name = get_settings().discovery_provider

    if provider_name == "csv":
        from .providers.csv_provider import CsvDiscoveryProvider

        return CsvDiscoveryProvider()

    if provider_name == "apollo":
        from .providers.apollo import ApolloDiscoveryProvider

        api_key = get_settings().apollo_api_key
        if not api_key:
            raise RuntimeError(
                "APOLLO_API_KEY is not set - required to run discovery against "
                "the real Apollo provider. Set it in .env, or pass an explicit "
                "discovery_provider to run_discovery() for testing."
            )
        return ApolloDiscoveryProvider(api_key=api_key)

    raise RuntimeError(
        f"Unknown DISCOVERY_PROVIDER='{provider_name}' - expected 'csv' or 'apollo'."
    )


def run_discovery(
    run_id: uuid.UUID,
    icp_config_id: str,
    session: Session,
    *,
    provider: Optional[DiscoveryProvider] = None,
    max_results: Optional[int] = None,
) -> dict[str, Any]:
    with log_context(run_id=str(run_id)):
        icp = load_icp_config(icp_config_id)
        criteria = criteria_from_icp(icp)
        active_provider = provider or get_default_discovery_provider()
        result_cap = max_results if max_results is not None else get_settings().discovery_max_results

        logger.info(
            "discovery started for icp_config_id=%s (industries=%s, geographies=%s, "
            "employee_count=%s-%s, max_results=%s)",
            icp_config_id,
            criteria.industries,
            criteria.geographies,
            criteria.employee_count_min,
            criteria.employee_count_max,
            result_cap,
        )

        summary = {
            "pages_fetched": 0,
            "raw_organizations_seen": 0,
            "persisted_new": 0,
            "skipped_duplicate": 0,
            "skipped_unmappable": 0,
        }

        page_number = 1
        while True:
            page = call_with_retry(
                lambda: active_provider.search_accounts(
                    criteria, page=page_number, per_page=PER_PAGE
                ),
                session=session,
                run_id=run_id,
                provider="apollo",
                operation="discovery",
            )
            summary["pages_fetched"] += 1

            for org in page.organizations:
                summary["raw_organizations_seen"] += 1

                # The Account row (if any) is created/resolved *before* the
                # ProviderRecord that references it - Postgres enforces the
                # foreign key, unlike this project's SQLite test default,
                # which silently allows FK violations. Real-Postgres
                # verification caught this ordering bug; see ADR-006 and
                # the technical audit's note on why that gap gets closed.
                resolved_account_id: Optional[uuid.UUID] = None

                if org.account is None:
                    summary["skipped_unmappable"] += 1
                    logger.info("skipped unmappable discovery result: %s", org.skip_reason)
                else:
                    existing = (
                        session.query(AccountORM)
                        .filter_by(domain=org.account.domain)
                        .one_or_none()
                    )
                    if existing is not None:
                        summary["skipped_duplicate"] += 1
                        resolved_account_id = existing.id
                        logger.info(
                            "skipped duplicate account, already known: domain=%s",
                            org.account.domain,
                        )
                    else:
                        account_orm = AccountORM(
                            id=org.account.id,
                            domain=org.account.domain,
                            name=org.account.name,
                            industry=org.account.industry,
                            employee_count=org.account.employee_count,
                            geography=org.account.geography,
                            status=org.account.status,
                            discovered_in_run_id=run_id,
                        )
                        session.add(account_orm)
                        session.flush()  # persisted immediately - see module docstring
                        resolved_account_id = account_orm.id
                        summary["persisted_new"] += 1

                session.add(
                    ProviderRecordORM(
                        account_id=resolved_account_id,
                        provider="apollo",
                        operation="discovery",
                        payload=org.raw_payload,
                    )
                )
                session.flush()

            results_so_far = summary["raw_organizations_seen"]
            reached_result_cap = results_so_far >= result_cap
            reached_last_page = page_number >= page.total_pages or not page.organizations

            if reached_result_cap or reached_last_page:
                break
            page_number += 1

        logger.info("discovery completed with summary=%s", summary)
        return summary
