"""Orchestrates decision-maker discovery for a run: finds RESEARCHED
accounts that haven't had a people-search attempt yet, and searches each
for contacts matching the configured persona.

Resumability note - this stage is different from every other one: Step 4's
AccountStatus state machine has no dedicated status for "people search
attempted" (RESEARCHED only legally transitions to QUALIFIED/
NOT_QUALIFIED - that's Step 15's job). Rather than add a new status column
value for something the existing audit trail already covers, "has this
account been searched yet" is answered by whether a ProviderRecord with
operation='people_discovery' exists for it - the same mechanism every
other stage already uses to keep a record of what happened, repurposed
here as the completion marker itself. A account with zero matching
contacts still gets a ProviderRecord (payload noting zero matches), so
it's correctly treated as "already searched, found nothing" rather than
retried forever.

Per-item failure isolation, same as enrichment/research: one account's
search call failing doesn't create a ProviderRecord for it, so it's
naturally picked up again on a later run - no dedicated failure status
needed for the same reason no dedicated success status is needed.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging import log_context
from app.orm import AccountORM, ContactORM, ProviderRecordORM
from infra.retry import NonRetryableError, RetryableError, call_with_retry
from prospectforge.models.enums import AccountStatus
from prospectforge.persona import load_persona_config

from .interface import PersonDiscoveryProvider, PersonSearchCriteria

logger = logging.getLogger("prospectforge.people_discovery")

PER_PAGE = 25


def get_default_person_discovery_provider() -> PersonDiscoveryProvider:
    """Factory for the provider used when the caller doesn't inject one -
    same pattern as every other stage's default-provider factory."""

    provider_name = get_settings().people_discovery_provider

    if provider_name == "csv":
        from .providers.csv_provider import CsvPersonDiscoveryProvider

        return CsvPersonDiscoveryProvider()

    if provider_name == "apollo":
        from .providers.apollo import ApolloPersonDiscoveryProvider

        api_key = get_settings().apollo_api_key
        if not api_key:
            raise RuntimeError(
                "APOLLO_API_KEY is not set - required to run people discovery "
                "against the real Apollo provider."
            )
        return ApolloPersonDiscoveryProvider(api_key=api_key)

    raise RuntimeError(
        f"Unknown PEOPLE_DISCOVERY_PROVIDER='{provider_name}' - expected 'csv' or 'apollo'."
    )


def run_people_discovery(
    run_id: uuid.UUID,
    session: Session,
    *,
    persona_id: Optional[str] = None,
    provider: Optional[PersonDiscoveryProvider] = None,
) -> Dict[str, Any]:
    with log_context(run_id=str(run_id)):
        persona = load_persona_config(persona_id or get_settings().people_discovery_persona_id)
        active_provider = provider or get_default_person_discovery_provider()
        criteria = PersonSearchCriteria(
            seniority_keywords=persona.seniority_keywords,
            department_keywords=persona.department_keywords,
        )

        summary = {
            "accounts_evaluated": 0,
            "accounts_with_contacts": 0,
            "accounts_with_no_matches": 0,
            "contacts_found": 0,
            "search_failed": 0,
        }

        candidates = (
            session.query(AccountORM)
            .filter(AccountORM.status == AccountStatus.RESEARCHED)
            .filter(
                ~AccountORM.id.in_(
                    session.query(ProviderRecordORM.account_id).filter(
                        ProviderRecordORM.operation == "people_discovery"
                    )
                )
            )
            .all()
        )

        for account_orm in candidates:
            with log_context(account_id=str(account_orm.id)):
                summary["accounts_evaluated"] += 1
                from app.mappers import orm_to_account

                account = orm_to_account(account_orm)

                try:
                    page = call_with_retry(
                        lambda: active_provider.search_people(
                            account, criteria, page=1, per_page=PER_PAGE
                        ),
                        session=session,
                        run_id=run_id,
                        account_id=account_orm.id,
                        provider="apollo",
                        operation="people_discovery",
                    )
                except (RetryableError, NonRetryableError) as exc:
                    summary["search_failed"] += 1
                    logger.info(
                        "people discovery failed for domain=%s: %s", account_orm.domain, exc
                    )
                    session.flush()
                    continue

                contacts_created = 0
                for person in page.people:
                    if person.contact is None:
                        session.add(
                            ProviderRecordORM(
                                account_id=account_orm.id,
                                provider="apollo",
                                operation="people_discovery",
                                payload={**person.raw_payload, "matched_rule": person.matched_rule},
                            )
                        )
                        logger.info(
                            "skipped unmappable person result for domain=%s: %s",
                            account_orm.domain,
                            person.skip_reason,
                        )
                        continue

                    contact_orm = ContactORM(
                        id=person.contact.id,
                        account_id=account_orm.id,
                        name=person.contact.name,
                        title=person.contact.title,
                        department=person.contact.department,
                        status=person.contact.status,
                    )
                    session.add(contact_orm)
                    session.flush()
                    session.add(
                        ProviderRecordORM(
                            account_id=account_orm.id,
                            contact_id=contact_orm.id,
                            provider="apollo",
                            operation="people_discovery",
                            payload={**person.raw_payload, "matched_rule": person.matched_rule},
                        )
                    )
                    contacts_created += 1
                    logger.info(
                        "found candidate contact: domain=%s name=%s title=%s matched_rule=%s",
                        account_orm.domain,
                        contact_orm.name,
                        contact_orm.title,
                        person.matched_rule,
                    )

                if contacts_created == 0:
                    # Still record the attempt - this is what keeps a
                    # zero-match account from being retried forever (see
                    # module docstring).
                    session.add(
                        ProviderRecordORM(
                            account_id=account_orm.id,
                            provider="apollo",
                            operation="people_discovery",
                            payload={"matched_count": 0, "total_entries_seen": page.total_entries},
                        )
                    )
                    summary["accounts_with_no_matches"] += 1
                    logger.info("no matching contacts found for domain=%s", account_orm.domain)
                else:
                    summary["accounts_with_contacts"] += 1
                    summary["contacts_found"] += contacts_created

                session.flush()

        logger.info("people discovery completed with summary=%s", summary)
        return summary
